import logging
from fastapi import APIRouter, Depends, HTTPException, status
from middleware.auth_middleware import get_current_user
from storage import schedule_repository
from pydantic import BaseModel
from typing import Literal

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audit/schedule", tags=["Schedule"])


class CreateScheduleRequest(BaseModel):
    url:       str
    keyword:   str
    frequency: Literal["weekly", "monthly"] = "weekly"


@router.get("")
async def list_schedules(user_id: str = Depends(get_current_user)):
    return schedule_repository.list_schedules(user_id)


@router.get("/for")
async def get_schedule_for(url: str, keyword: str, user_id: str = Depends(get_current_user)):
    """Return the active schedule for a specific URL+keyword, or null."""
    sched = schedule_repository.get_schedule(user_id, url, keyword)
    return sched or {}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(body: CreateScheduleRequest, user_id: str = Depends(get_current_user)):
    existing = schedule_repository.get_schedule(user_id, body.url.strip(), body.keyword.strip())
    if existing:
        return existing
    return schedule_repository.create_schedule(user_id, body.url.strip(), body.keyword.strip(), body.frequency)


@router.delete("/{id}")
async def delete_schedule(id: str, user_id: str = Depends(get_current_user)):
    deleted = schedule_repository.delete_schedule(id, user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Schedule {id} not found.")
    return {"ok": True}
