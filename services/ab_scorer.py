"""
services/ab_scorer.py
=====================
Scores alternative title/meta variants by re-running the XGBoost classifier
with the swapped feature values. No URL re-scraping needed- uses on_page data
stored at audit time to reconstruct a representative feature vector.
"""

import logging
from services.feature_extractor import keyword_in_text, classify_intent
from services.predictor import predict

logger = logging.getLogger(__name__)

_COMPARISON_FEATS = [
    "cc_referring_domains_log", "cc_pagerank", "cc_harmonic_centrality",
    "opr_page_rank", "opr_rank_log", "domain_frequency_log",
    "lighthouse_seo_score", "technical_score", "tfidf_relevance",
    "word_count", "internal_link_count", "keyword_density",
    "keyword_frequency", "keyword_proximity_score", "keyword_prominence_score",
    "semantic_relevance", "keyword_variations_count",
]


def _zero_query_features() -> dict:
    return {
        **{f"{f}_vs_query_z":   0.0 for f in _COMPARISON_FEATS},
        **{f"{f}_vs_query_pct": 0.0 for f in _COMPARISON_FEATS},
    }


def reconstruct_features(on_page: dict, keyword: str) -> dict:
    """
    Reconstruct a model-compatible feature dict from stored on_page data.
    Fields that can't be recovered default to conservative values.
    """
    kw       = keyword.strip()
    kw_words = kw.split()
    title    = on_page.get("title", "")
    meta     = on_page.get("meta_description", "")
    wc       = on_page.get("word_count", 0)
    img_cnt  = on_page.get("image_count", 0)
    alt_cnt  = on_page.get("images_with_alt", 0)
    h1_kw    = 1 if on_page.get("h1_has_kw") else 0
    alt_kw   = 1 if on_page.get("alt_has_kw") else 0
    t_kw     = 1 if on_page.get("title_has_kw") else 0
    m_kw     = 1 if on_page.get("meta_has_kw") else 0
    kw_sig   = t_kw + m_kw + h1_kw + alt_kw

    kw_dens  = on_page.get("keyword_density", 0)
    kw_freq  = max(1, int((kw_dens / 100) * wc)) if wc > 0 and kw_dens > 0 else 0
    title_len = on_page.get("title_length", len(title))
    meta_len  = on_page.get("meta_desc_length", len(meta))

    has_schema = 1 if on_page.get("has_schema_markup") else 0
    canonical  = on_page.get("canonical", "")
    lighthouse = on_page.get("lighthouse_score", -1)
    tech_score = (
        1 +          # assume has_viewport
        1 +          # assume is_https
        has_schema +
        (1 if canonical else 0)
    )
    tfidf      = round(kw_freq / max(1, wc ** 0.5), 4) if wc > 0 else 0

    return {
        "url": canonical, "domain": "", "keyword": kw,
        "page_title": title, "meta_description": meta,
        "title_length": title_len, "title_has_keyword": t_kw,
        "keyword_position_in_title": (title.lower().find(kw.lower()) if t_kw else -1),
        "meta_desc_length": meta_len, "meta_desc_has_keyword": m_kw, "meta_desc_present": 1 if meta else 0,
        "h1_count": 1 if on_page.get("h1") else 0, "h1_has_keyword": h1_kw,
        "h2_count": on_page.get("h2_count", 0), "h3_count": on_page.get("h3_count", 0),
        "total_heading_count": 1 + on_page.get("h2_count", 0) + on_page.get("h3_count", 0),
        "word_count": wc, "keyword_frequency": kw_freq, "keyword_density": kw_dens,
        "has_images": 1 if img_cnt > 0 else 0, "image_count": img_cnt,
        "images_with_alt_count": alt_cnt, "alt_has_keyword": alt_kw,
        "paragraph_count": on_page.get("paragraph_count", 0),
        "internal_link_count": on_page.get("internal_links", 0),
        "external_link_count": on_page.get("external_links", 0),
        "has_viewport_meta": 1, "is_https": 1,
        "has_schema_markup": has_schema, "has_canonical_tag": 1 if canonical else 0,
        "technical_score": tech_score, "keyword_signal_count": kw_sig,
        "optimal_title_length": 1 if 50 <= title_len <= 60 else 0,
        "optimal_meta_length": 1 if 120 <= meta_len <= 160 else 0,
        "alt_coverage": round(alt_cnt / img_cnt, 4) if img_cnt > 0 else 0,
        "heading_density": round((1 + on_page.get("h2_count", 0) + on_page.get("h3_count", 0)) / max(wc, 1), 6),
        "keyword_word_count": len(kw_words), "is_long_tail": 1 if len(kw_words) >= 4 else 0,
        "query_intent": classify_intent(kw),
        "keyword_exact_match": 1 if kw_freq > 0 else 0,
        "keyword_exact_match_count": kw_freq,
        "keyword_in_first_100_words": 1 if on_page.get("body_has_kw") else 0,
        "keyword_proximity_score": 0.5, "keyword_variations_count": len(kw_words),
        "tfidf_relevance": tfidf, "keyword_prominence_score": round(kw_sig / 3, 4),
        "semantic_relevance": 0.5, "lighthouse_seo_score": lighthouse,
        "opr_page_rank": 0.0, "opr_rank_log": 0.0, "opr_domain_found": 0,
        "domain_frequency": 0, "domain_frequency_log": 0.0,
        "cc_pagerank": 0.0, "cc_harmonic_centrality": 0.0,
        "cc_referring_domains_log": 0.0, "cc_found": 0,
        "relevance_to_authority_ratio": 0.0, "semantic_to_authority_ratio": 0.0,
        "keyword_competition": 0.0, "keyword_avg_position": 0.0, "keyword_position_std": 0.0,
        "raw_html_size_kb": 50.0, "total_dom_elements": 200,
        "js_files_count": 3, "css_files_count": 2,
        "has_og_tags": 1, "has_robots_meta": 0,
        "text_to_html_ratio": 0.2, "dom_depth": 10,
        "missing_img_dimensions": 0, "modern_img_ratio": 0.0,
        **_zero_query_features(),
    }


def _apply_variant(base: dict, title: str, meta: str, keyword: str) -> dict:
    f   = dict(base)
    kw  = keyword.strip()
    h1k = f.get("h1_has_keyword", 0)
    alk = 1 if f.get("alt_has_keyword", 0) > 0 else 0

    f["page_title"]               = title
    f["title_length"]             = len(title)
    f["title_has_keyword"]        = keyword_in_text(kw, title)
    f["optimal_title_length"]     = 1 if 50 <= len(title) <= 60 else 0
    f["keyword_position_in_title"] = (title.lower().find(kw.lower()) if kw.lower() in title.lower() else -1)

    f["meta_description"]         = meta
    f["meta_desc_length"]         = len(meta)
    f["meta_desc_has_keyword"]    = keyword_in_text(kw, meta)
    f["meta_desc_present"]        = 1 if meta.strip() else 0
    f["optimal_meta_length"]      = 1 if 120 <= len(meta) <= 160 else 0

    kw_sig = f["title_has_keyword"] + f["meta_desc_has_keyword"] + h1k + alk
    f["keyword_signal_count"]     = kw_sig
    f["keyword_prominence_score"] = round(kw_sig / 3, 4)
    return f


def score_variants(on_page: dict, keyword: str, variants: list[dict]) -> list[dict]:
    """
    Score each title/meta variant using the classifier.
    Returns results sorted best → worst (HIGH quality / lowest rank first).
    """
    base     = reconstruct_features(on_page, keyword)
    external = {"lighthouse_score": on_page.get("lighthouse_score", -1)}
    results  = []

    for i, v in enumerate(variants):
        title = (v.get("title") or "").strip()
        meta  = (v.get("meta_description") or "").strip()
        if not title and not meta:
            continue

        eff_title = title or on_page.get("title", "")
        eff_meta  = meta  or on_page.get("meta_description", "")
        features  = _apply_variant(base, eff_title, eff_meta, keyword)

        try:
            pred    = predict(features, external)
            quality = pred["classification"]["quality"]
            rank    = round(pred.get("regression", {}).get("predicted_rank", 50))
        except Exception as e:
            logger.warning(f"Variant {i} prediction failed: {e}")
            quality, rank = "LOW", 50

        results.append({
            "variant":          chr(65 + i),
            "title":            eff_title,
            "meta_description": eff_meta,
            "quality":          quality,
            "predicted_rank":   rank,
            "title_length":     len(eff_title),
            "meta_length":      len(eff_meta),
            "title_has_kw":     bool(keyword_in_text(keyword, eff_title)),
            "meta_has_kw":      bool(keyword_in_text(keyword, eff_meta)),
            "optimal_title":    50 <= len(eff_title) <= 60,
            "optimal_meta":     120 <= len(eff_meta) <= 160,
        })

    quality_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    results.sort(key=lambda r: (quality_rank.get(r["quality"], 3), r["predicted_rank"]))
    return results
