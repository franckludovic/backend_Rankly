"""
api/routes_competitor_watch.py
================================
Routes for competitor monitoring (watch list CRUD).

Endpoints:
  GET    /api/competitors/watch             - list all watches for user
  GET    /api/competitors/watch/check       - check single URL+keyword
  POST   /api/competitors/watch             - add/toggle a watch
  DELETE /api/competitors/watch/{id}        - remove a watch
  POST   /api/competitors/watch/run-check   - manually trigger check (dev/admin)
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from middleware.auth_middleware import get_current_user
from storage import competitor_watch_repository as repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/competitors", tags=["competitor-watch"])


class AddWatchRequest(BaseModel):
    url:              str
    keyword:          str
    source_audit_id:  str | None = None
    initial_title:    str | None = None
    initial_word_count: int | None = None


@router.get("/watch")
async def list_watches(
    keyword: str | None = None,
    user_id: str = Depends(get_current_user),
):
    return {"watches": repo.list_watches(user_id, keyword=keyword)}


@router.get("/watch/check")
async def check_watch(
    url:     str,
    keyword: str,
    user_id: str = Depends(get_current_user),
):
    watch   = repo.get_watch(user_id, url, keyword)
    return {"watching": watch is not None, "watch": watch}


@router.post("/watch")
async def add_watch(
    body: AddWatchRequest,
    user_id: str = Depends(get_current_user),
):
    watch   = repo.add_watch(
        user_id          = user_id,
        url              = body.url,
        keyword          = body.keyword,
        source_audit_id  = body.source_audit_id,
        initial_title    = body.initial_title,
        initial_word_count = body.initial_word_count,
    )
    return {"watch": watch}


@router.delete("/watch/{watch_id}")
async def remove_watch(
    watch_id: str,
    user_id: str = Depends(get_current_user),
):
    repo.remove_watch(watch_id, user_id)
    return {"deleted": watch_id}


@router.post("/watch/run-check")
async def run_check_now(user_id: str = Depends(get_current_user)):
    """Manual trigger for dev/testing. Runs the full check for ALL users' watches."""
    from services.competitor_monitor import check_all_watches
    await check_all_watches()
    return {"status": "check complete"}
