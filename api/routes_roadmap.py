from fastapi import APIRouter, Depends, HTTPException, status
from middleware.auth_middleware import get_current_user
from schemas.roadmap import RoadmapTaskStatusUpdate
from storage import audit_repository, roadmap_repository

router = APIRouter(prefix="/api/roadmap", tags=["Roadmap"])

@router.patch("/{audit_id}/task/{task_id}")
async def update_task_status(
    audit_id: str,
    task_id: str,
    body: RoadmapTaskStatusUpdate,
    user_id: str = Depends(get_current_user)
):
    # Verify ownership
    audit = audit_repository.get_audit(audit_id, user_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit {audit_id} not found.")

    updated_task = roadmap_repository.update_task_status(audit_id, task_id, body.status)
    if not updated_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task {task_id} not found in audit {audit_id}.")
    
    return updated_task
