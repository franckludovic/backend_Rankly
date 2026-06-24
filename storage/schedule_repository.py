from datetime import datetime, timezone, timedelta
from services.supabase_client import supabase

TABLE = "scheduled_audits"


def list_schedules(user_id: str) -> list[dict]:
    res = supabase.table(TABLE).select("*").eq("user_id", user_id).order("created_at", desc=False).execute()
    return res.data or []


def get_schedule(user_id: str, url: str, keyword: str) -> dict | None:
    res = (
        supabase.table(TABLE)
        .select("*")
        .eq("user_id", user_id)
        .eq("url", url)
        .eq("keyword", keyword)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def create_schedule(user_id: str, url: str, keyword: str, frequency: str) -> dict:
    delta = timedelta(days=7 if frequency == "weekly" else 30)
    data  = {
        "user_id":    user_id,
        "url":        url,
        "keyword":    keyword,
        "frequency":  frequency,
        "next_run_at": (datetime.now(timezone.utc) + delta).isoformat(),
        "enabled":    True,
    }
    res = supabase.table(TABLE).insert(data).execute()
    return res.data[0] if res.data else {}


def delete_schedule(schedule_id: str, user_id: str) -> bool:
    res = supabase.table(TABLE).delete().eq("id", schedule_id).eq("user_id", user_id).execute()
    return len(res.data) > 0


def get_due_schedules() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    res = (
        supabase.table(TABLE)
        .select("*")
        .eq("enabled", True)
        .lte("next_run_at", now)
        .execute()
    )
    return res.data or []


def update_next_run(schedule_id: str, frequency: str) -> None:
    delta    = timedelta(days=7 if frequency == "weekly" else 30)
    next_run = (datetime.now(timezone.utc) + delta).isoformat()
    supabase.table(TABLE).update({"next_run_at": next_run}).eq("id", schedule_id).execute()
