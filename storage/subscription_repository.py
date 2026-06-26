"""
Supabase CRUD for the `subscriptions` table.

Run this SQL once in your Supabase SQL editor:

    CREATE TABLE IF NOT EXISTS subscriptions (
        id                           uuid DEFAULT gen_random_uuid() PRIMARY KEY,
        user_id                      uuid REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE NOT NULL,
        ls_customer_id               text,
        ls_subscription_id           text,
        plan                         text DEFAULT 'free' NOT NULL,
        status                       text DEFAULT 'active' NOT NULL,
        current_period_end           timestamptz,
        dev_addon                    boolean DEFAULT false NOT NULL,
        ls_dev_addon_subscription_id text,
        created_at                   timestamptz DEFAULT now(),
        updated_at                   timestamptz DEFAULT now()
    );

    ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

    CREATE POLICY "Users can read own subscription"
        ON subscriptions FOR SELECT TO authenticated
        USING (auth.uid() = user_id);

-- If the table already exists, run these migrations instead:
--   ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS dev_addon boolean DEFAULT false NOT NULL;
--   ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS ls_dev_addon_subscription_id text;
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


def get_by_ls_dev_addon_subscription(ls_subscription_id: str) -> dict | None:
    result = (
        supabase.table("subscriptions")
        .select("*")
        .eq("ls_dev_addon_subscription_id", ls_subscription_id)
        .maybe_single()
        .execute()
    )
    return result.data


def has_api_access(user_id: str) -> bool:
    """Return True if the user's plan includes API key access.

    Agency and Business always have access.
    Pro users need the Developer Add-on subscription active.
    """
    try:
        sub = get_subscription(user_id)
        if not sub:
            return False
        active = sub.get("status") in ("active", "trialing")
        plan   = sub.get("plan", "free") if active else "free"
        if plan in ("agency", "business"):
            return True
        if plan == "pro" and sub.get("dev_addon") is True:
            return True
    except Exception as e:
        logger.warning(f"Could not check API access for {user_id}: {e}")
    return False
