-- Rankly- complete schema
-- Run in the Supabase SQL editor in the order shown.
-- auth.users is managed by Supabase Auth- do not create it manually.

-- ─── Shared trigger function ──────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

-- ─── user_profiles ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.user_profiles (
  id            UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name  TEXT,
  avatar_url    TEXT,
  plan          TEXT        NOT NULL DEFAULT 'trial'
                CHECK (plan IN ('trial', 'pro', 'enterprise')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.user_profiles (id, display_name, avatar_url)
  VALUES (
    NEW.id,
    NEW.raw_user_meta_data->>'full_name',
    NEW.raw_user_meta_data->>'avatar_url'
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE PROCEDURE public.handle_new_user();

-- ─── audits ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.audits (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  url         TEXT        NOT NULL,
  keyword     TEXT        NOT NULL,
  response    JSONB       NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audits_user_created ON public.audits (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audits_keyword      ON public.audits (user_id, keyword);
CREATE INDEX IF NOT EXISTS idx_audits_response_gin ON public.audits USING GIN (response);

-- ─── roadmap_tasks ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.roadmap_tasks (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  audit_id    UUID        NOT NULL REFERENCES public.audits(id) ON DELETE CASCADE,
  task_data   JSONB       NOT NULL,
  status      TEXT        NOT NULL DEFAULT 'todo'
              CHECK (status IN ('todo', 'in_progress', 'done', 'skipped')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_roadmap_tasks_audit_id ON public.roadmap_tasks (audit_id);

CREATE TRIGGER roadmap_tasks_updated_at
  BEFORE UPDATE ON public.roadmap_tasks
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- ─── usage_counters ───────────────────────────────────────────────────────────
-- Exists for audit/reference- quota is now counted from usage_events.
-- This table is NOT written by backend code in the current implementation.
CREATE TABLE IF NOT EXISTS public.usage_counters (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_type   TEXT        NOT NULL CHECK (subject_type IN ('user', 'device')),
  subject_id     TEXT        NOT NULL,
  product        TEXT        NOT NULL CHECK (product IN ('main_app', 'extension')),
  mode           TEXT        NOT NULL CHECK (mode IN ('offline', 'online', 'n/a')),
  used_count     INT         NOT NULL DEFAULT 0 CHECK (used_count >= 0),
  limit_count    INT         NOT NULL CHECK (limit_count > 0),
  reset_policy   TEXT        NOT NULL DEFAULT 'never'
                 CHECK (reset_policy IN ('never', 'monthly', 'weekly')),
  last_reset_at  TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (subject_type, subject_id, product, mode)
);

CREATE TRIGGER usage_counters_updated_at
  BEFORE UPDATE ON public.usage_counters
  FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

-- ─── usage_events ─────────────────────────────────────────────────────────────
-- Append-only. Monthly quota is counted via: SELECT count(*) WHERE consumed_at >= month_start.
CREATE TABLE IF NOT EXISTS public.usage_events (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key  TEXT        NOT NULL UNIQUE,
  subject_type     TEXT        NOT NULL CHECK (subject_type IN ('user', 'device')),
  subject_id       TEXT        NOT NULL,
  product          TEXT        NOT NULL CHECK (product IN ('main_app', 'extension')),
  mode             TEXT        NOT NULL CHECK (mode IN ('offline', 'online', 'n/a')),
  audit_id         UUID        REFERENCES public.audits(id) ON DELETE SET NULL,
  consumed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_idempotency ON public.usage_events (idempotency_key);
CREATE INDEX        IF NOT EXISTS idx_usage_events_subject      ON public.usage_events (subject_type, subject_id, product, mode, consumed_at);

-- ─── Row Level Security ───────────────────────────────────────────────────────
ALTER TABLE public.user_profiles  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audits         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.roadmap_tasks  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_events   ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own profile"
  ON public.user_profiles FOR ALL
  USING (auth.uid() = id);

CREATE POLICY "Users manage own audits"
  ON public.audits FOR ALL
  USING (auth.uid() = user_id);

CREATE POLICY "Users manage own roadmap tasks"
  ON public.roadmap_tasks FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.audits
      WHERE audits.id = roadmap_tasks.audit_id
        AND audits.user_id = auth.uid()
    )
  );

-- usage_counters and usage_events: backend service_role only- no frontend access.
