"""
storage/competitor_watch_repository.py
=======================================
CRUD for the watched_competitors table.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from services.supabase_client import supabase

logger = logging.getLogger(__name__)


def list_watches(user_id: str, keyword: str | None = None) -> list[dict]:
    q = supabase.table("watched_competitors").select("*").eq("user_id", user_id).order("created_at", desc=True)
    if keyword:
        q = q.eq("keyword", keyword)
    return (q.execute().data or [])


def get_watch(user_id: str, url: str, keyword: str) -> dict | None:
    rows = (
        supabase.table("watched_competitors")
        .select("*")
        .eq("user_id", user_id)
        .eq("competitor_url", url)
        .eq("keyword", keyword)
        .execute()
        .data or []
    )
    return rows[0] if rows else None


def add_watch(
    user_id: str,
    url: str,
    keyword: str,
    source_audit_id: str | None = None,
    initial_title: str | None = None,
    initial_word_count: int | None = None,
) -> dict:
    existing = get_watch(user_id, url, keyword)
    if existing:
        return existing
    row = {
        "user_id":          user_id,
        "competitor_url":   url,
        "keyword":          keyword,
        "source_audit_id":  source_audit_id,
        "last_title":       initial_title,
        "last_word_count":  initial_word_count,
        "last_checked_at":  datetime.now(timezone.utc).isoformat(),
    }
    result = supabase.table("watched_competitors").insert(row).execute()
    return (result.data or [{}])[0]


def remove_watch(watch_id: str, user_id: str) -> bool:
    supabase.table("watched_competitors").delete().eq("id", watch_id).eq("user_id", user_id).execute()
    return True


def update_snapshot(watch_id: str, title: str | None, word_count: int | None) -> None:
    supabase.table("watched_competitors").update({
        "last_title":       title,
        "last_word_count":  word_count,
        "last_checked_at":  datetime.now(timezone.utc).isoformat(),
    }).eq("id", watch_id).execute()


def get_all_watches() -> list[dict]:
    """All watches across all users- used by the scheduler."""
    return (supabase.table("watched_competitors").select("*").execute().data or [])


def get_user_email(user_id: str) -> str | None:
    try:
        resp = supabase.auth.admin.get_user_by_id(user_id)
        return resp.user.email if resp and resp.user else None
    except Exception as e:
        logger.warning(f"Could not fetch user email for {user_id}: {e}")
        return None
