from services.supabase_client import supabase

def bulk_insert_tasks(audit_id: str, tasks: list) -> list:
    rows = []
    for task in tasks:
        rows.append({
            "audit_id": audit_id,
            "task_data": task,
            "status": "todo"
        })
    if not rows:
        return []
    result = supabase.table("roadmap_tasks").insert(rows).execute()
    return result.data or []

def update_task_status(audit_id: str, task_id: str, status: str) -> dict:
    result = supabase.table("roadmap_tasks").update({"status": status}).eq("id", task_id).eq("audit_id", audit_id).execute()
    if result.data:
        return result.data[0]
    return {}

def get_tasks_by_audit(audit_id: str) -> list:
    result = supabase.table("roadmap_tasks").select("*").eq("audit_id", audit_id).execute()
    return result.data or []
