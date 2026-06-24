"""
services/predictor.py
=====================
Builds feature vectors and runs both ML models.
Handles all preprocessing (log transforms, encoding, NaN fill).

V6 notes:
  - Classifier uses 51 features (on-page + semantic).
  - Regressor uses 97 features (adds OPR, CC, domain-freq, competition,
    and query-relative z-score / percentile features).
  - domain_frequency_log is already log1p'd by the extractor- NOT in LOG_FEATURES.
  - cc_referring_domains_log is already log1p'd by training pipeline- NOT in LOG_FEATURES.
"""

import numpy as np
import pandas as pd
import logging
from models.model_registry import registry

logger = logging.getLogger(__name__)

# Features that need log1p transform (raw counts → log scale, same as training notebook)
# Note: domain_frequency_log and cc_referring_domains_log are ALREADY log1p'd in the
# feature extractor, so they must NOT appear here (would double-transform them).
LOG_FEATURES = [
    "word_count",
    "image_count",
    "paragraph_count",
    "internal_link_count",
    "external_link_count",
    "raw_html_size_kb",
    "total_dom_elements",
    "js_files_count",
    "css_files_count",
    "opr_page_rank",
]


def encode_kpit(val) -> int:
    """Encode keyword_position_in_title: 'absent' → 0."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return 0
    if str(val).lower() in ("absent", "none", ""):
        return 0
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def build_feature_vector(features: dict, feature_list: list) -> pd.DataFrame:
    """
    Build a single-row DataFrame in the exact column order the model expects.
    Fills missing features with 0.  Applies all preprocessing.
    """
    row = {}
    for col in feature_list:
        val = features.get(col, 0)

        # Handle keyword_position_in_title string encoding
        if col == "keyword_position_in_title":
            val = encode_kpit(val)

        # Ensure numeric
        try:
            val = float(val) if val is not None else 0.0
        except (ValueError, TypeError):
            val = 0.0

        row[col] = val

    df = pd.DataFrame([row])

    # Log transform skewed raw-count features
    for col in LOG_FEATURES:
        if col in df.columns:
            df[col] = np.log1p(df[col].clip(lower=0))

    # Fix NaN / inf
    df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

    return df


def _enrich_with_external(features: dict, external: dict) -> dict:
    """
    Merge external API results (Lighthouse + OPR) into the feature dict
    and recompute derived fields that depend on them.
    """
    # Lighthouse
    lh_score = external.get("lighthouse_score", -1)
    features["lighthouse_seo_score"] = lh_score

    # OPR
    features["opr_page_rank"]    = external.get("opr_page_rank", 0)
    features["opr_rank_log"]     = external.get("opr_rank_log", 0)
    features["opr_domain_found"] = external.get("opr_domain_found", 0)

    # Common Crawl graph
    features["cc_found"]                 = external.get("cc_found", features.get("cc_found", 0))
    features["cc_pagerank"]              = external.get("cc_pagerank", features.get("cc_pagerank", 0.0))
    features["cc_harmonic_centrality"]   = external.get(
        "cc_harmonic_centrality",
        features.get("cc_harmonic_centrality", 0.0),
    )
    features["cc_referring_domains_log"] = external.get(
        "cc_referring_domains_log",
        features.get("cc_referring_domains_log", 0.0),
    )

    # Recompute authority ratios now that OPR / CC values may have changed.
    # If cc_pagerank is unavailable (0), use a tiny floor and cap the result.
    cc_pr = max(float(features.get("cc_pagerank", 0.0)), 1e-9)
    tfidf = features.get("tfidf_relevance", 0.0)
    sem   = features.get("semantic_relevance", 0.0)
    denom = cc_pr
    features["relevance_to_authority_ratio"] = round(
        min(tfidf / denom, 1e6), 6
    )
    features["semantic_to_authority_ratio"]  = round(
        min(sem / denom, 1e6), 6
    )

    return features


def run_classification(features: dict) -> dict:
    """
    Run classification model.
    Returns quality label, confidence, and probabilities per class.
    """
    if not registry.loaded:
        raise RuntimeError("Models not loaded. Call registry.load() at startup.")

    X = build_feature_vector(features, registry.clf_features)

    pred_enc = registry.clf_model.predict(X)[0]
    quality  = registry.label_encoder.inverse_transform([pred_enc])[0]
    probas   = registry.clf_model.predict_proba(X)[0]
    classes  = registry.label_encoder.classes_

    proba_dict = {cls: round(float(p) * 100, 1) for cls, p in zip(classes, probas)}
    confidence = round(float(max(probas)) * 100, 1)

    return {
        "quality"       : quality,
        "confidence"    : confidence,
        "probabilities" : proba_dict,
        "model_accuracy": f"{registry.clf_accuracy * 100:.1f}%",
    }


def run_regression(features: dict) -> dict:
    """
    Run regression model.
    Returns predicted rank and honest interpretation.
    """
    if not registry.reg_model:
        return {
            "predicted_rank": None,
            "available"     : False,
            "reason"        : "Regression model not loaded",
        }

    X = build_feature_vector(features, registry.reg_features)

    pred_log  = registry.reg_model.predict(X)[0]
    pred_rank = int(np.clip(np.round(np.expm1(pred_log)), 1, 30))

    if pred_rank <= 10:
        tier = "top 10"
    elif pred_rank <= 20:
        tier = "positions 11–20"
    else:
        tier = "positions 21–30"

    return {
        "predicted_rank": pred_rank,
        "tier"          : tier,
        "available"     : True,
        "r2_score"      : registry.reg_r2,
        "disclaimer"    : (
            f"Predicted rank: ~{pred_rank} ({tier}). "
            f"Note: rank prediction has R²={registry.reg_r2:.2f}- "
            "on-page features explain limited ranking variance. "
            "Off-page factors (backlinks, domain authority) "
            "are the primary ranking drivers."
        ),
    }


def predict(features: dict, external_signals: dict | None = None) -> dict:
    """
    Full prediction pipeline.
    Merges external signals (Lighthouse, OPR) into features,
    then runs classification + regression.

    Parameters
    ----------
    features : dict
        Output of extract_features_from_html / extract_features_from_url.
    external_signals : dict | None
        Output of fetch_all_external_signals.  Pass None for local/HTML analysis.
    """
    if external_signals is None:
        external_signals = {}

    # Merge external signals and update derived features
    features = _enrich_with_external(features, external_signals)

    lighthouse_available = external_signals.get("lighthouse_available", False)

    if not lighthouse_available:
        accuracy_note = (
            "46.5% accuracy- Lighthouse score unavailable. "
            "Deploy page or use public URL for full 83.8% analysis."
        )
    else:
        accuracy_note = "83.8% accuracy- full feature set"

    clf_result = run_classification(features)
    reg_result = run_regression(features)

    return {
        "classification" : clf_result,
        "regression"     : reg_result,
        "accuracy_note"  : accuracy_note,
        "features_used"  : len(registry.clf_features),
    }
