"""
api/routes_developer.py
=========================
API key management endpoints (Developer Access feature).

  GET    /api/developer/keys         - list keys for current user
  POST   /api/developer/keys         - create new key (returns plaintext once)
  DELETE /api/developer/keys/{id}    - revoke a key

Access rules:
  - Agency / Business plans: included
  - Pro plan + Developer Add-on: included
  - Free / Pro without add-on: 403
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from middleware.auth_middleware import get_current_user
from storage import api_key_repository as repo
from storage import subscription_repository as sub_repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])


async def _require_api_access(user_id: str = Depends(get_current_user)) -> str:
    if not sub_repo.has_api_access(user_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "API key access requires an Agency or Business plan, "
                "or the Developer Add-on on Pro."
            ),
        )
    return user_id


class CreateKeyRequest(BaseModel):
    name: str


@router.get("/keys")
async def list_keys(user_id: str = Depends(_require_api_access)):
    return {"keys": repo.list_keys(user_id)}


@router.post("/keys")
async def create_key(body: CreateKeyRequest, user_id: str = Depends(_require_api_access)):
    result = repo.create_key(user_id, body.name.strip() or "My API Key")
    return {
        "key":    result["key"],
        "record": result["record"],
        "note":   "Store this key securely- it will not be shown again.",
    }


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: str, user_id: str = Depends(_require_api_access)):
    repo.revoke_key(key_id, user_id)
    return {"revoked": key_id}
