import uuid
import logging
import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from services.quota_service import QuotaService, UsageLimitError
from services.predictor import predict
from models.model_registry import registry
from services.recommender import generate_recommendations

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/extension", tags=["Extension"])

class ExtensionAuditRequest(BaseModel):
    url: str
    keyword: str
    features: dict
    idempotency_key: str

def _build_extension_response(features: dict, prediction: dict) -> dict:
    """Assemble the standard analysis response."""
    quality = prediction["classification"]["quality"]
    recommendations = generate_recommendations(features, quality)

    # Collect display-friendly on-page summary
    on_page = {
        "title"             : features.get("page_title", ""),
        "title_length"      : features.get("title_length", 0),
        "meta_description"  : features.get("meta_description", ""),
        "meta_desc_length"  : features.get("meta_desc_length", 0),
        "h1_text"           : features.get("h1_text", ""),
        "word_count"        : features.get("word_count", 0),
        "keyword_density"   : features.get("keyword_density", 0),
        "semantic_relevance": features.get("semantic_relevance", 0),
        "technical_score"   : features.get("technical_score", 0),
        "is_https"          : bool(features.get("is_https", 0)),
        "has_schema_markup" : bool(features.get("has_schema_markup", 0)),
        "has_canonical_tag" : bool(features.get("has_canonical_tag", 0)),
        "has_og_tags"       : bool(features.get("has_og_tags", 0)),
        "internal_links"    : features.get("internal_link_count", 0),
        "external_links"    : features.get("external_link_count", 0),
        "image_count"       : features.get("image_count", 0),
        "images_with_alt"   : features.get("images_with_alt_count", 0),
        "lighthouse_score"  : features.get("lighthouse_seo_score", -1),
    }

    return {
        "url"             : features.get("url", ""),
        "keyword"         : features.get("keyword", ""),
        "on_page"         : on_page,
        "prediction"      : prediction,
        "recommendations" : recommendations,
    }

@router.post("/audit/online")
async def extension_audit_online(
    body: ExtensionAuditRequest,
    authorization: Optional[str] = Header(None),
    x_device_id: Optional[str] = Header(None)
):
    if not registry.loaded:
        raise HTTPException(503, "Models not loaded yet. Please wait and retry.")

    url = body.url.strip()
    keyword = body.keyword.strip()
    
    if not url or not keyword:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="url and keyword are required.")

    # Determine subject type and ID
    subject_type = "device"
    subject_id = x_device_id or ""
    
    if authorization and authorization.startswith("Bearer "):
        try:
            from services.supabase_client import supabase
            token = authorization.removeprefix("Bearer ")
            user_response = supabase.auth.get_user(token)
            subject_type = "user"
            subject_id = str(user_response.user.id)
        except Exception:
            if not subject_id:
                raise HTTPException(status_code=401, detail="Invalid token and no X-Device-Id provided")

    if not subject_id:
        raise HTTPException(status_code=400, detail="X-Device-Id header is required for anonymous extension calls.")

    # Quota check and consume
    try:
        res = QuotaService.check_and_consume(
            subject_type=subject_type,
            subject_id=subject_id,
            product="extension",
            mode="online",
            idempotency_key=body.idempotency_key
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

    # Perform analysis
    features = dict(body.features)
    features.setdefault("keyword", keyword)
    features.setdefault("url", url)
    features.setdefault("domain", "")

    try:
        prediction = predict(features, external_signals=None)
    except Exception as e:
        logger.exception("Prediction failed (extension online mode)")
        raise HTTPException(500, f"Prediction failed: {e}")

    resp = _build_extension_response(features, prediction)
    resp["id"] = str(uuid.uuid4())
    resp["created_at"] = datetime.datetime.now().isoformat()
    return resp
