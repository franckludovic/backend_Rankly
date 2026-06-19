"""
api/routes.py
=============
FastAPI routes for the SEO Suggestion Engine.

Endpoints
---------
GET  /health                  — Server health + model status
GET  /models/status           — Detailed model registry info
POST /analyse/url             — Full pipeline: scrape → extract → external APIs → predict → recommend
POST /analyse/html            — HTML analysis (browser extension raw-HTML mode)
POST /analyse/features        — Features-only analysis (extension pre-extraction mode)
POST /admin/reload-models     — Hot-reload models without restarting (requires secret header)
"""

import logging
import statistics
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, HttpUrl
from typing import Optional

from config import RELOAD_SECRET
from models.model_registry import registry
from services.feature_extractor import extract_features_from_url, extract_features_from_html
from services.external_apis import fetch_all_external_signals
from services.predictor import predict
from services.recommender import generate_recommendations
from services.serp_competitor import run_competitor_pipeline, compute_query_relative_features

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════

class UrlRequest(BaseModel):
    url: str
    keyword: str

    model_config = {"json_schema_extra": {
        "example": {
            "url": "https://example.com/blog/seo-tips",
            "keyword": "SEO tips for beginners",
        }
    }}


class HtmlRequest(BaseModel):
    html: str
    url: str            # used for domain extraction + link analysis
    keyword: str

    model_config = {"json_schema_extra": {
        "example": {
            "html": "<html><head><title>My Page</title></head><body>...</body></html>",
            "url": "https://example.com/page",
            "keyword": "best SEO tools",
        }
    }}


class FeaturesRequest(BaseModel):
    """
    Accept a pre-extracted feature dict directly from the browser extension.
    All fields are optional — missing ones default to 0 inside the predictor.
    """
    keyword: str
    url: Optional[str] = ""
    features: dict


# ═══════════════════════════════════════════════════════════════════
# HEALTH & STATUS
# ═══════════════════════════════════════════════════════════════════

@router.get("/health", tags=["System"])
async def health():
    """Quick liveness check."""
    return {
        "status"   : "ok",
        "models"   : registry.loaded,
        "semantic" : registry.semantic_model is not None,
    }


@router.get("/models/status", tags=["System"])
async def models_status():
    """Detailed model registry status."""
    return registry.status()


# ═══════════════════════════════════════════════════════════════════
# ANALYSIS PIPELINES
# ═══════════════════════════════════════════════════════════════════

def _build_response(features: dict, prediction: dict) -> dict:
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


@router.post("/analyse/url", tags=["Analysis"])
async def analyse_url(body: UrlRequest):
    """
    Full analysis pipeline for a live public URL.

    1. Scrape the URL and extract on-page features.
    2. Fetch Lighthouse + OPR scores in parallel.
    3. Run classification + regression models.
    4. Return predictions + prioritised recommendations.
    """
    if not registry.loaded:
        raise HTTPException(503, "Models not loaded yet. Please wait and retry.")

    url     = str(body.url).strip()
    keyword = body.keyword.strip()

    if not keyword:
        raise HTTPException(400, "keyword must not be empty.")

    # ── 1. Feature extraction (may raise ValueError) ───────────────
    try:
        features = extract_features_from_url(url, keyword)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception(f"Unexpected error scraping {url}")
        raise HTTPException(500, f"Scraping failed: {e}")

    # ── 2. External signals (Lighthouse + OPR, parallel) ──────────
    is_local = url.startswith(("http://localhost", "http://127."))
    try:
        external = await fetch_all_external_signals(url, features["domain"], is_local)
    except Exception as e:
        logger.warning(f"External APIs failed: {e}")
        external = {}

    # ── 2b. SERP competitor pipeline (query-relative z-score/pct) ─
    try:
        competitors = await run_competitor_pipeline(keyword, target_url=url)

        # Target values for comparison should include already-fetched external signals
        target_for_query = dict(features)
        target_for_query["lighthouse_seo_score"]      = external.get("lighthouse_score", -1)
        target_for_query["opr_page_rank"]             = external.get("opr_page_rank", 0.0)
        target_for_query["opr_rank_log"]              = external.get("opr_rank_log", 0.0)
        target_for_query["cc_pagerank"]               = external.get("cc_pagerank", 0.0)
        target_for_query["cc_harmonic_centrality"]    = external.get("cc_harmonic_centrality", 0.0)
        target_for_query["cc_referring_domains_log"]  = external.get("cc_referring_domains_log", 0.0)

        features.update(compute_query_relative_features(target_for_query, competitors))

        # Keep competition summary fields consistent with available competitor pool
        competitor_count = len(competitors)
        features["keyword_competition"] = round(float(competitor_count), 4)
        if competitor_count > 0:
            positions = list(range(1, competitor_count + 1))
            features["keyword_avg_position"] = round(sum(positions) / competitor_count, 4)
            features["keyword_position_std"] = round(
                statistics.pstdev(positions) if competitor_count > 1 else 0.0,
                6,
            )
        else:
            features["keyword_avg_position"] = 0.0
            features["keyword_position_std"] = 0.0

    except Exception as e:
        logger.warning(f"SERP competitor pipeline failed: {e}")

    # ── 3. Predict ─────────────────────────────────────────────────
    try:
        prediction = predict(features, external)
    except Exception as e:
        logger.exception("Prediction failed")
        raise HTTPException(500, f"Prediction failed: {e}")

    return _build_response(features, prediction)


@router.post("/analyse/html", tags=["Analysis"])
async def analyse_html(body: HtmlRequest):
    """
    Analyse raw HTML (for browser extension or local file testing).

    External APIs (Lighthouse, OPR) are skipped — the extension
    should pass the page's public URL in `url` if it wants partial
    external signals, or pass localhost if not applicable.
    """
    if not registry.loaded:
        raise HTTPException(503, "Models not loaded yet. Please wait and retry.")

    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(400, "keyword must not be empty.")
    if not body.html.strip():
        raise HTTPException(400, "html must not be empty.")

    try:
        features = extract_features_from_html(body.html, body.url, keyword)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception("HTML feature extraction failed")
        raise HTTPException(500, f"HTML extraction failed: {e}")

    try:
        prediction = predict(features, external_signals=None)
    except Exception as e:
        logger.exception("Prediction failed")
        raise HTTPException(500, f"Prediction failed: {e}")

    return _build_response(features, prediction)


@router.post("/analyse/features", tags=["Analysis"])
async def analyse_features(body: FeaturesRequest):
    """
    Accept a pre-extracted features dict (browser extension pre-extraction mode).
    The extension computes features client-side and sends them here for prediction.
    Missing features default to 0.
    """
    if not registry.loaded:
        raise HTTPException(503, "Models not loaded yet. Please wait and retry.")

    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(400, "keyword must not be empty.")

    # Merge keyword + url into the features dict so the response is complete
    features = dict(body.features)
    features.setdefault("keyword", keyword)
    features.setdefault("url", body.url or "")
    features.setdefault("domain", "")

    try:
        prediction = predict(features, external_signals=None)
    except Exception as e:
        logger.exception("Prediction failed (features mode)")
        raise HTTPException(500, f"Prediction failed: {e}")

    return _build_response(features, prediction)


# ═══════════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════════

@router.post("/admin/reload-models", tags=["Admin"])
async def reload_models(x_reload_secret: Optional[str] = Header(None)):
    """
    Hot-reload all models from disk without restarting the server.
    Requires the X-Reload-Secret header to match RELOAD_SECRET in config.
    """
    if x_reload_secret != RELOAD_SECRET:
        raise HTTPException(403, "Invalid or missing X-Reload-Secret header.")
    try:
        registry.reload()
        return {"status": "ok", "message": "Models reloaded successfully."}
    except Exception as e:
        logger.exception("Model reload failed")
        raise HTTPException(500, f"Reload failed: {e}")
