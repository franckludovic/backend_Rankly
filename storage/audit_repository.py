from collections import defaultdict
from services.supabase_client import supabase

def create_audit(user_id: str, url: str, keyword: str, response: dict) -> dict:
    data = {
        "user_id": user_id,
        "url": url,
        "keyword": keyword,
        "response": response
    }
    result = supabase.table("audits").insert(data).execute()
    if result.data:
        return result.data[0]
    return {}

def get_audit(audit_id: str, user_id: str) -> dict:
    result = supabase.table("audits").select("*").eq("id", audit_id).eq("user_id", user_id).execute()
    if result.data:
        return result.data[0]
    return {}

def delete_audit(audit_id: str, user_id: str) -> bool:
    result = supabase.table("audits").delete().eq("id", audit_id).eq("user_id", user_id).execute()
    return len(result.data) > 0

def list_audits(user_id: str, limit: int = 50, offset: int = 0) -> list:
    result = supabase.table("audits").select("*").eq("user_id", user_id).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


def get_score_history(user_id: str, url: str, keyword: str) -> list[dict]:
    """Return score-over-time series for a URL+keyword pair, oldest first."""
    result = (
        supabase.table("audits")
        .select("id,created_at,response")
        .eq("user_id", user_id)
        .eq("url", url)
        .eq("keyword", keyword)
        .order("created_at", desc=False)
        .execute()
    )
    history = []
    for a in result.data or []:
        resp  = a.get("response", {})
        pred  = resp.get("prediction", {})
        on_p  = resp.get("on_page", {})
        qual  = (pred.get("classification") or {}).get("quality", "LOW")
        base  = {"HIGH": 85, "MEDIUM": 60}.get(qual, 35)
        tech  = (on_p.get("technical_score") or 0) * 5
        score = min(100, base + tech)
        rank  = round((pred.get("regression") or {}).get("predicted_rank", 50))
        history.append({
            "id":             a["id"],
            "date":           a["created_at"][:10],
            "created_at":     a["created_at"],
            "seo_score":      round(score),
            "quality":        qual,
            "predicted_rank": rank,
        })
    return history


def find_cannibalization(user_id: str) -> list[dict]:
    """
    Find keywords where the same user has audited more than one distinct URL.
    Returns list of {keyword, pages: [{id, url, created_at, quality, score}]},
    sorted so the highest-quality page is first in each pages list.
    """
    result = (
        supabase.table("audits")
        .select("id,url,keyword,created_at,response")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    audits = result.data or []

    by_kw: dict[str, list] = defaultdict(list)
    for a in audits:
        by_kw[a["keyword"].strip().lower()].append(a)

    _quality_rank = {"High": 0, "MEDIUM": 1, "Medium": 1, "LOW": 2, "Low": 2, "Unknown": 3}

    def _extract_page(a: dict) -> dict:
        pred = (a.get("response") or {}).get("prediction") or {}
        quality = pred.get("quality_label") or pred.get("label") or "Unknown"
        score   = pred.get("predicted_score") or pred.get("seo_score") or 0
        return {
            "id":         a["id"],
            "url":        a["url"],
            "created_at": a["created_at"],
            "quality":    quality,
            "score":      score,
        }

    conflicts = []
    for kw_lower, group in by_kw.items():
        # latest audit per URL (group is already newest-first)
        url_best: dict[str, dict] = {}
        for a in group:
            if a["url"] not in url_best:
                url_best[a["url"]] = a

        if len(url_best) < 2:
            continue

        pages = [_extract_page(a) for a in url_best.values()]
        pages.sort(key=lambda p: (_quality_rank.get(p["quality"], 3), -(p["score"] or 0)))

        conflicts.append({
            "keyword": group[0]["keyword"],  # original casing
            "pages":   pages,
        })

    conflicts.sort(key=lambda c: -len(c["pages"]))
    return conflicts
