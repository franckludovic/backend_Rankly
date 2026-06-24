-- 002_watched_competitors.sql
-- Run once in the Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS watched_competitors (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  source_audit_id  UUID        REFERENCES audits(id) ON DELETE SET NULL,
  competitor_url   TEXT        NOT NULL,
  keyword          TEXT        NOT NULL,
  last_title       TEXT,
  last_word_count  INTEGER,
  last_checked_at  TIMESTAMPTZ,
  created_at       TIMESTAMPTZ DEFAULT now(),
  UNIQUE (user_id, competitor_url, keyword)
);

ALTER TABLE watched_competitors ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own watched competitors"
  ON watched_competitors FOR ALL
  USING  (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE INDEX IF NOT EXISTS idx_watched_competitors_user
  ON watched_competitors (user_id);
