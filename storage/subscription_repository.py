"""
Supabase CRUD for the `subscriptions` table.

Run this SQL once in your Supabase SQL editor:

    CREATE TABLE IF NOT EXISTS subscriptions (
        id                   uuid DEFAULT gen_random_uuid() PRIMARY KEY,
        user_id              uuid REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE NOT NULL,
        ls_customer_id       text,
        ls_subscription_id   text,
        plan                 text DEFAULT 'free' NOT NULL,
        status               text DEFAULT 'active' NOT NULL,
        current_period_end   timestamptz,
        created_at           timestamptz DEFAULT now(),
        updated_at           timestamptz DEFAULT now()
    );

    ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

    CREATE POLICY "Users can read own subscription"
        ON subscriptions FOR SELECT TO authenticated
        USING (auth.uid() = user_id);
"""

import logging
from services.supabase_client import supabase

logger = logging.getLogger(__name__)


def get_subscription(user_id: str) -> dict | None:
    result = (
        supabase.table("subscriptions")
        .select("*")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return result.data


def get_plan(user_id: str) -> str:
    """Return the user's current plan name, defaulting to 'free'."""
    try:
        sub = get_subscription(user_id)
        if sub and sub.get("status") in ("active", "trialing"):
            return sub.get("plan", "free")
    except Exception as e:
        logger.warning(f"Could not fetch plan for {user_id}: {e}")
    return "free"


def upsert_subscription(user_id: str, data: dict) -> None:
    data["user_id"]    = user_id
    data["updated_at"] = "now()"
    (
        supabase.table("subscriptions")
        .upsert(data, on_conflict="user_id")
        .execute()
    )


def get_by_ls_customer(ls_customer_id: str) -> dict | None:
    result = (
        supabase.table("subscriptions")
        .select("*")
        .eq("ls_customer_id", ls_customer_id)
        .maybe_single()
        .execute()
    )
    return result.data


def get_by_ls_subscription(ls_subscription_id: str) -> dict | None:
    result = (
        supabase.table("subscriptions")
        .select("*")
        .eq("ls_subscription_id", ls_subscription_id)
        .maybe_single()
        .execute()
    )
    return result.data
