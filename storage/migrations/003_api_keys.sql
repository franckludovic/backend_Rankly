-- 003_api_keys.sql
-- Run once in the Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS api_keys (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name         TEXT        NOT NULL,
  key_hash     TEXT        NOT NULL UNIQUE,   -- SHA-256 of the full key
  key_prefix   TEXT        NOT NULL,          -- first 12 chars (display only)
  created_at   TIMESTAMPTZ DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  revoked      BOOLEAN     NOT NULL DEFAULT false
);

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own api keys"
  ON api_keys FOR ALL
  USING  (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE INDEX IF NOT EXISTS idx_api_keys_user    ON api_keys (user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash    ON api_keys (key_hash) WHERE NOT revoked;
