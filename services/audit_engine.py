import logging
import statistics
from models.model_registry import registry
from services.feature_extractor import extract_features_from_url
from services.external_apis import fetch_all_external_signals
from services.predictor import predict
from services.recommender import generate_recommendations
from services.serp_competitor import run_competitor_pipeline, compute_query_relative_features
from services.schema_generator import generate_schema

logger = logging.getLogger(__name__)

def _readability_score(features: dict) -> int:
    wc = features.get("word_count", 0)
    pc = features.get("paragraph_count", 1) or 1
    avg_words = wc / pc
    # Simple proxy: penalise dense paragraphs above 20 words each
    return max(0, min(100, round(100 - max(0, avg_words - 20) * 1.2)))


def _build_on_page(features: dict) -> dict:
    return {
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
        "h2_count"          : features.get("h2_count", 0),
        "h3_count"          : features.get("h3_count", 0),
        "paragraph_count"   : features.get("paragraph_count", 0),
        "readability_score" : _readability_score(features),
        "canonical"         : features.get("canonical_url", ""),
        "index_status"      : features.get("index_status", "index, follow"),
        # Fixed: use correct feature_extractor key names (were all returning False)
        "title_has_kw"      : bool(features.get("title_has_keyword", 0)),
        "meta_has_kw"       : bool(features.get("meta_desc_has_keyword", 0)),
        "h1_has_kw"         : bool(features.get("h1_has_keyword", 0)),
        "alt_has_kw"        : bool(features.get("alt_has_keyword", 0)),
        "body_has_kw"       : bool(features.get("keyword_exact_match", 0)),
    }

async def run_full_analysis(url: str, keyword: str) -> dict:
    if not registry.loaded:
        raise ValueError("Models not loaded yet. Please wait and retry.")

    url     = url.strip()
    keyword = keyword.strip()

    if not keyword:
        raise ValueError("keyword must not be empty.")

    # 1. Feature extraction
    features = extract_features_from_url(url, keyword)

    generated_schema = generate_schema(
        page_type=features.get("page_type", "WebPage"),
        features=features,
        url=url,
        keyword=keyword,
        context=features.get("schema_context"),
    )

    # 2. External signals (Lighthouse + OPR, parallel)
    is_local = url.startswith(("http://localhost", "http://127."))
    try:
        external = await fetch_all_external_signals(url, features["domain"], is_local)
    except Exception as e:
        logger.warning(f"External APIs failed: {e}")
        external = {}

    # 2b. SERP competitor pipeline
    competitors   = []
    serp_features = []
    try:
        competitors, serp_features = await run_competitor_pipeline(keyword, target_url=url)

        # Target values for comparison should include already-fetched external signals
        target_for_query = dict(features)
        target_for_query["lighthouse_seo_score"]      = external.get("lighthouse_score", -1)
        target_for_query["opr_page_rank"]             = external.get("opr_page_rank", 0.0)
        target_for_query["opr_rank_log"]              = external.get("opr_rank_log", 0.0)
        target_for_query["cc_pagerank"]               = external.get("cc_pagerank", 0.0)
        target_for_query["cc_harmonic_centrality"]    = external.get("cc_harmonic_centrality", 0.0)
        target_for_query["cc_referring_domains_log"]  = external.get("cc_referring_domains_log", 0.0)

        features.update(compute_query_relative_features(target_for_query, competitors))

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

    # 3. Predict
    try:
        prediction = predict(features, external)
    except Exception as e:
        logger.exception("Prediction failed")
        raise ValueError(f"Prediction failed: {e}")

    # Build response nested shape
    quality = prediction["classification"]["quality"]
    recommendations = generate_recommendations(features, quality)
    on_page = _build_on_page(features)

    # Format competitors to return to frontend
    formatted_competitors = []
    for idx, c in enumerate(competitors):
        # altCoverage: percentage alt tag coverage
        img_cnt = c.get("image_count", 0)
        alt_cnt = c.get("alt_count", 0) or c.get("images_with_alt_count", 0)
        alt_cov = round((alt_cnt / img_cnt * 100), 1) if img_cnt > 0 else 0.0

        formatted_competitors.append({
            "rank": idx + 1,
            "domain": c.get("domain", ""),
            "url": c.get("url", ""),
            "title": c.get("page_title", ""),
            "wordCount": c.get("word_count", 0),
            "keywordDensity": c.get("keyword_density", 0),
            "keywordSignal": c.get("keyword_signal_count", 0),
            "technicalScore": c.get("technical_score", 0),
            "altCoverage": alt_cov,
            "internalLinks": c.get("internal_link_count", 0),
            "externalLinks": c.get("external_link_count", 0),
            "hasSchema": bool(c.get("has_schema_markup", 0)),
            "titleHasKw": bool(c.get("title_has_kw", 0)),
            "metaHasKw": bool(c.get("meta_desc_has_kw", 0)),
            "h1HasKw": bool(c.get("h1_has_kw", 0)),
            "altHasKw": bool(c.get("alt_has_kw", 0)),
            "bodyHasKw": bool(c.get("keyword_exact_match", 0)),
            "searchPresence": max(5, 100 - (idx * 10)),
            "h2Count": c.get("h2_count", 0),
            "h3Count": c.get("h3_count", 0),
        })

    return {
        "url": url,
        "keyword": keyword,
        "on_page": on_page,
        "prediction": prediction,
        "recommendations": recommendations,
        "competitors": formatted_competitors,
        "serp_features": serp_features,
        "generated_schema": generated_schema,
    }
