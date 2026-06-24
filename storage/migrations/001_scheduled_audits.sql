-- Run once in Supabase SQL Editor to create the scheduled_audits table.
-- Dashboard → SQL Editor → New query → paste → Run

create table if not exists scheduled_audits (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  url          text not null,
  keyword      text not null,
  frequency    text not null check (frequency in ('weekly', 'monthly')),
  next_run_at  timestamptz not null,
  enabled      boolean not null default true,
  created_at   timestamptz not null default now()
);

-- One active schedule per user+url+keyword combination
create unique index if not exists scheduled_audits_unique
  on scheduled_audits (user_id, url, keyword)
  where enabled = true;

-- Row-level security: users can only see/edit their own schedules
alter table scheduled_audits enable row level security;

create policy "Users manage own schedules"
  on scheduled_audits for all
  using  (auth.uid() = user_id)
  with check (auth.uid() = user_id);
