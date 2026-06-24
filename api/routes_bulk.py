import uuid
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from middleware.auth_middleware import get_current_user
from pydantic import BaseModel
from typing import Optional
from services.bulk_auditor import BulkJob, parse_sitemap

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audit/bulk", tags=["Bulk Audit"])

# In-memory job store- one dict per server process
_jobs: dict[str, BulkJob] = {}
_MAX_URLS = 50


class BulkRequest(BaseModel):
    keyword:     str
    sitemap_url: Optional[str] = None
    urls:        Optional[list[str]] = None


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_bulk_audit(
    body: BulkRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
):
    """Start a bulk audit job. Returns job_id immediately; poll GET /{job_id} for progress."""
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    urls: list[str] = [u.strip() for u in (body.urls or []) if u.strip()]

    if body.sitemap_url:
        try:
            sitemap_urls = await parse_sitemap(body.sitemap_url.strip())
            urls = list(dict.fromkeys(urls + sitemap_urls))  # dedupe, preserve order
        except Exception as e:
            logger.warning(f"Sitemap parse failed for {body.sitemap_url}: {e}")
            if not urls:
                raise HTTPException(status_code=422, detail=f"Could not parse sitemap: {e}")

    urls = urls[:_MAX_URLS]
    if not urls:
        raise HTTPException(status_code=422, detail="Provide at least one URL or a valid sitemap_url.")

    job_id = str(uuid.uuid4())
    job    = BulkJob(job_id=job_id, user_id=user_id, keyword=keyword)
    _jobs[job_id] = job

    background_tasks.add_task(job.run, urls)
    return {"job_id": job_id, "total": len(urls), "keyword": keyword}


@router.get("/{job_id}")
async def get_bulk_job(job_id: str, user_id: str = Depends(get_current_user)):
    """Poll this endpoint for job progress and results."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden.")
    return job.snapshot()
