import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from middleware.auth_middleware import get_current_user
from schemas.audit import AuditRequest, AuditResponse
from services.audit_engine import run_full_analysis
from services.quota_service import QuotaService, UsageLimitError
from storage import audit_repository, roadmap_repository
from typing import Optional
from pydantic import BaseModel
from typing import List

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audit", tags=["Audit"])

class AuditGenerateRequest(BaseModel):
    url: str
    keyword: str
    idempotency_key: Optional[str] = None

class VariantItem(BaseModel):
    title: Optional[str] = ""
    meta_description: Optional[str] = ""

class ABScoreRequest(BaseModel):
    variants: List[VariantItem]

@router.post("/generate", response_model=AuditResponse)
async def generate_audit(body: AuditGenerateRequest, user_id: str = Depends(get_current_user)):
    url = body.url.strip()
    keyword = body.keyword.strip()
    
    if not url or not keyword:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="url and keyword are required.")

    # Generate or reuse idempotency key
    idem_key = body.idempotency_key or f"app_{user_id}_{uuid.uuid4()}"

    # 1. Quota Check and Consume
    try:
        quota_res = QuotaService.check_and_consume(
            subject_type="user",
            subject_id=user_id,
            product="main_app",
            mode="online",
            idempotency_key=idem_key
        )
    except UsageLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": e.message,
                "code": e.code,
                "usage": {
                    "remaining": e.remaining,
                    "limit": e.limit
                }
            }
        )

    # 2. Run analysis
    try:
        response_data = await run_full_analysis(url, keyword)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.exception("Audit generation failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Analysis failed: {str(e)}")

    # 3. Persist audit to DB
    try:
        audit_record = audit_repository.create_audit(user_id, url, keyword, response_data)
        if not audit_record:
            raise Exception("No record returned from DB write")
    except Exception as e:
        logger.exception("Failed to save audit to database")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error: {str(e)}")

    audit_id = audit_record["id"]
    created_at = audit_record["created_at"]

    # 4. Insert Roadmap Tasks
    recs = []
    try:
        inserted_tasks = roadmap_repository.bulk_insert_tasks(audit_id, response_data.get("recommendations", []))
        for t in inserted_tasks:
            item = dict(t["task_data"])
            item["id"] = t["id"]
            item["status"] = t["status"]
            recs.append(item)
    except Exception as e:
        logger.warning(f"Failed to seed roadmap tasks for audit {audit_id}: {e}")
        recs = response_data.get("recommendations", [])

    # Build final response
    return AuditResponse(
        id=audit_id,
        url=url,
        keyword=keyword,
        created_at=created_at,
        on_page=response_data["on_page"],
        prediction=response_data["prediction"],
        recommendations=recs,
        competitors=response_data["competitors"],
        serp_features=response_data.get("serp_features", []),
        generated_schema=response_data.get("generated_schema"),
    )

@router.get("/score-history")
async def get_score_history(url: str, keyword: str, user_id: str = Depends(get_current_user)):
    """Return SEO score over time for a URL+keyword pair (uses existing audit records)."""
    history = audit_repository.get_score_history(user_id, url, keyword)
    return {"history": history, "count": len(history)}


@router.get("/cannibalization")
async def get_cannibalization(user_id: str = Depends(get_current_user)):
    """Return all keyword-cannibalization conflicts for the current user."""
    conflicts = audit_repository.find_cannibalization(user_id)
    return {"conflicts": conflicts, "count": len(conflicts)}


@router.get("/{id}", response_model=AuditResponse)
async def get_audit(id: str, user_id: str = Depends(get_current_user)):
    audit = audit_repository.get_audit(id, user_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit {id} not found.")
    
    resp_data = audit["response"]
    
    # Load roadmap tasks from DB to get their current status
    try:
        tasks = roadmap_repository.get_tasks_by_audit(id)
        recs = []
        for t in tasks:
            item = dict(t["task_data"])
            item["id"] = t["id"]
            item["status"] = t["status"]
            recs.append(item)
    except Exception as e:
        logger.warning(f"Failed to load roadmap tasks for audit {id}: {e}")
        recs = resp_data.get("recommendations", [])

    return AuditResponse(
        id=audit["id"],
        url=audit["url"],
        keyword=audit["keyword"],
        created_at=audit["created_at"],
        on_page=resp_data.get("on_page", {}),
        prediction=resp_data.get("prediction", {}),
        recommendations=recs,
        competitors=resp_data.get("competitors", []),
        serp_features=resp_data.get("serp_features", []),
        generated_schema=resp_data.get("generated_schema"),
    )

@router.post("/{id}/brief")
async def generate_brief(id: str, user_id: str = Depends(get_current_user)):
    """Generate a content brief using Gemini 2.0 Flash from existing audit data."""
    from services.brief_generator import generate_brief as _gen_brief
    audit = audit_repository.get_audit(id, user_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit {id} not found.")
    resp = audit["response"]
    try:
        brief = await _gen_brief(
            keyword       = audit["keyword"],
            url           = audit["url"],
            on_page       = resp.get("on_page", {}),
            competitors   = resp.get("competitors", []),
            serp_features = resp.get("serp_features", []),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Brief generation failed for audit {id}: {e}")
        raise HTTPException(status_code=502, detail=f"Brief generation failed: {str(e)}")
    return brief


@router.post("/{id}/ab-score")
async def ab_score_audit(id: str, body: ABScoreRequest, user_id: str = Depends(get_current_user)):
    """Score alternative title/meta variants against the audit's baseline features."""
    from services.ab_scorer import score_variants
    audit = audit_repository.get_audit(id, user_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit {id} not found.")
    resp_data = audit["response"]
    on_page   = resp_data.get("on_page", {})
    keyword   = audit["keyword"]
    variants  = [{"title": v.title or "", "meta_description": v.meta_description or ""} for v in body.variants]
    results   = score_variants(on_page, keyword, variants)
    return {"results": results, "keyword": keyword}


@router.delete("/{id}")
async def delete_audit(id: str, user_id: str = Depends(get_current_user)):
    deleted = audit_repository.delete_audit(id, user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit {id} not found or not owned by user.")
    return {"ok": True}
