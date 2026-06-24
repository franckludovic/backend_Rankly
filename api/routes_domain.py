import logging
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, Query
from middleware.auth_middleware import get_current_user
from storage import audit_repository
from services.linker import get_linking_suggestions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/domain", tags=["Domain"])


def _norm_domain(domain: str) -> str:
    if "://" not in domain:
        domain = "https://" + domain
    return urlparse(domain).netloc.lower().removeprefix("www.")


@router.get("/linking-suggestions")
async def linking_suggestions(
    domain:  str = Query(..., description="Domain to analyse, e.g. example.com"),
    user_id: str = Depends(get_current_user),
):
    """Return internal linking opportunities for all audited pages on a domain."""
    d       = _norm_domain(domain)
    audits  = audit_repository.list_audits(user_id, limit=100)
    result  = get_linking_suggestions(user_id, d, audits)
    return result
