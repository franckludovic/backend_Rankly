from services.supabase_client import supabase


def count_events_this_month(
    subject_type: str, subject_id: str, product: str, mode: str, month_start: str
) -> int:
    """Count usage events with consumed_at on or after month_start (ISO timestamp, UTC)."""
    result = (
        supabase.table("usage_events")
        .select("id", count="exact")
        .eq("subject_type", subject_type)
        .eq("subject_id", subject_id)
        .eq("product", product)
        .eq("mode", mode)
        .gte("consumed_at", month_start)
        .execute()
    )
    return result.count or 0


def check_idempotency(idempotency_key: str) -> bool:
    result = (
        supabase.table("usage_events")
        .select("id")
        .eq("idempotency_key", idempotency_key)
        .execute()
    )
    return len(result.data) > 0


def record_event(
    idempotency_key: str,
    subject_type: str,
    subject_id: str,
    product: str,
    mode: str,
    audit_id: str = None,
):
    data = {
        "idempotency_key": idempotency_key,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "product": product,
        "mode": mode,
    }
    if audit_id:
        data["audit_id"] = audit_id
    supabase.table("usage_events").insert(data).execute()
