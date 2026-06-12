"""
api/routes.py
=============
All API endpoints.

POST /analyse             <- main endpoint (URL scraping)
POST /analyse/extension   <- browser extension (pre-extracted features)
POST /analyse/file        <- HTML file upload (local dev pages)
GET  /health              <- health check
GET  /model/status        <- model info
POST /model/reload        <- hot-swap models without restart
"""

import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, validator

from models.model_registry import registry
from services.feature_extractor import (
    extract_features_from_url,
    extract_features_from_html,
    extract_domain,
)
from services.external_apis import fetch_all_external_signals
from services.predictor import predict
from services.recommender import generate_recommendations

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════

class AnalyseURLRequest(BaseModel):
    url     : str
    keyword : str
    is_local: bool = False

    @validator("keyword")
    def keyword_not_empty(cls, v):
        if not v.strip():
            raise ValueError("keyword cannot be empty")
        return v.strip()

    @validator("url")
    def url_not_empty(cls, v):
        if not v.strip():
            raise ValueError("url cannot be empty")
        return v.strip()


class AnalyseExtensionRequest(BaseModel):
    """Sent by browser extension — features already extracted from DOM."""
    features : dict
    keyword  : str
    url      : str
    is_local : bool = False


# ═══════════════════════════════════════════════════════════════
# SHARED ANALYSIS LOGIC
# ═══════════════════════════════════════════════════════════════

async def run_full_analysis(
    features : dict,
    keyword  : str,
    url      : str,
    is_local : bool
) -> dict:
    """
    Core pipeline shared by all endpoints:
    1. Fetch Lighthouse + OPR in parallel
    2. Merge into features
    3. Run classification + regression
    4. Generate recommendations
    5. Return structured response
    """
    domain = extract_domain(url)

    # External signals (parallel async calls)
    external = await fetch_all_external_signals(url, domain, is_local)

    # Merge external signals into features
    features["lighthouse_seo_score"] = external["lighthouse_score"]
    features["opr_page_rank"]        = external["opr_page_rank"]
    features["opr_rank_log"]         = external["opr_rank_log"]
    features["opr_domain_found"]     = external["opr_domain_found"]

    lighthouse_available = external["lighthouse_available"]

    # Run both models
    predictions = predict(features, lighthouse_available)
    quality     = predictions["classification"]["quality"]
    recs        = generate_recommendations(features, quality)

    # Analysis mode + disclaimer
    if is_local:
        mode       = "local"
        disclaimer = (
            "Local page — Lighthouse and domain authority unavailable. "
            "Deploy to a public URL for full 83.8% accuracy analysis."
        )
    elif not lighthouse_available:
        mode       = "online_no_lighthouse"
        disclaimer = (
            "Lighthouse unavailable (timeout or API limit). "
            "Using on-page features only — accuracy ~46.5%."
        )
    else:
        mode       = "full"
        disclaimer = None

    return {
        "url"               : url,
        "keyword"           : keyword,
        "domain"            : domain,
        "analysis_mode"     : mode,

        # Classification
        "quality"           : quality,
        "confidence"        : predictions["classification"]["confidence"],
        "probabilities"     : predictions["classification"]["probabilities"],
        "model_accuracy"    : predictions["classification"]["model_accuracy"],

        # Regression
        "predicted_rank"    : predictions["regression"].get("predicted_rank"),
        "rank_tier"         : predictions["regression"].get("tier"),
        "rank_available"    : predictions["regression"].get("available", False),
        "rank_disclaimer"   : predictions["regression"].get("disclaimer"),

        # External signals
        "lighthouse_score"      : external["lighthouse_score"],
        "lighthouse_available"  : lighthouse_available,
        "opr_page_rank"         : external["opr_page_rank"],

        # Key features for UI display
        "key_features"      : {
            "word_count"        : features.get("word_count", 0),
            "title_length"      : features.get("title_length", 0),
            "meta_desc_present" : features.get("meta_desc_present", 0),
            "meta_desc_length"  : features.get("meta_desc_length", 0),
            "h1_count"          : features.get("h1_count", 0),
            "has_schema_markup" : features.get("has_schema_markup", 0),
            "has_canonical_tag" : features.get("has_canonical_tag", 0),
            "keyword_density"   : round(features.get("keyword_density", 0), 2),
            "image_count"       : features.get("image_count", 0),
            "images_with_alt"   : features.get("images_with_alt_count", 0),
            "internal_links"    : features.get("internal_link_count", 0),
            "lighthouse_score"  : external["lighthouse_score"],
        },

        # Recommendations
        "recommendations"       : recs,
        "recommendation_count"  : len(recs),

        # Meta
        "disclaimer"            : disclaimer,
        "features_used"         : predictions["features_used"],
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@router.post("/analyse")
async def analyse_url(request: AnalyseURLRequest):
    """
    Main endpoint. Scrapes URL + runs full pipeline.
    Used by web dashboard.
    """
    try:
        features = extract_features_from_url(request.url, request.keyword)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Feature extraction error: {e}")
        raise HTTPException(status_code=500, detail=f"Could not analyse URL: {str(e)}")

    return await run_full_analysis(
        features, request.keyword, request.url, request.is_local
    )


@router.post("/analyse/extension")
async def analyse_from_extension(request: AnalyseExtensionRequest):
    """
    Extension endpoint. Features already extracted from DOM by extension.
    Faster than /analyse — no HTTP request to target URL needed.
    Works for localhost, staging, and live URLs.
    """
    features = dict(request.features)
    features["keyword"] = request.keyword

    return await run_full_analysis(
        features, request.keyword, request.url, request.is_local
    )


@router.post("/analyse/file")
async def analyse_html_file(
    file   : UploadFile = File(...),
    keyword: str        = Form(...),
    url    : str        = Form(default="https://localhost/")
):
    """
    HTML file upload endpoint.
    User uploads .html file — no live URL needed.
    Always treated as local (no Lighthouse / OPR).
    """
    if not file.filename.endswith((".html", ".htm")):
        raise HTTPException(
            status_code=422,
            detail="Only .html or .htm files are supported"
        )

    try:
        html  = (await file.read()).decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read file: {e}")

    try:
        features = extract_features_from_html(html, url, keyword.strip())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return await run_full_analysis(
        features, keyword.strip(), url, is_local=True
    )


@router.get("/health")
async def health():
    """Health check for EC2 load balancer / monitoring."""
    return {"status": "ok", "models_loaded": registry.loaded}


@router.get("/model/status")
async def model_status():
    """Returns current model info — confirm a swap worked."""
    return registry.status()


@router.post("/model/reload")
async def reload_models():
    """
    Hot-reload models without restarting server.
    Call after replacing .joblib files in models/ folder.
    """
    try:
        registry.reload()
        return {"status": "reloaded", "models": registry.status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reload failed: {str(e)}")
