from fastapi import APIRouter, Depends, Query
from middleware.auth_middleware import get_current_user
from storage import audit_repository
from schemas.audit import AuditResponse
from typing import List

router = APIRouter(prefix="/api/history", tags=["History"])

@router.get("", response_model=List[AuditResponse])
async def get_history(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user)
):
    audits = audit_repository.list_audits(user_id, limit, offset)
    resp = []
    for audit in audits:
        resp_data = audit["response"]
        resp.append(AuditResponse(
            id=audit["id"],
            url=audit["url"],
            keyword=audit["keyword"],
            created_at=audit["created_at"],
            on_page=resp_data.get("on_page", {}),
            prediction=resp_data.get("prediction", {}),
            recommendations=resp_data.get("recommendations", []),
            competitors=resp_data.get("competitors", [])
        ))
    return resp
