"""
services/predictor.py
=====================
Builds feature vectors and runs both ML models.
Handles all preprocessing (log transforms, encoding, NaN fill).
"""

import numpy as np
import pandas as pd
import logging
from models.model_registry import registry

logger = logging.getLogger(__name__)

# Features that need log1p transform (same as in your notebook)
LOG_FEATURES = [
    "word_count", "image_count", "paragraph_count",
    "internal_link_count", "external_link_count",
    "raw_html_size_kb", "total_dom_elements",
    "js_files_count", "css_files_count",
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
    Build a single-row DataFrame in the exact column order
    the model expects. Fills missing features with 0.
    Applies all preprocessing.
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

    # Log transform skewed features
    for col in LOG_FEATURES:
        if col in df.columns:
            df[col] = np.log1p(df[col].clip(lower=0))

    # Fix NaN / inf
    df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

    return df


def run_classification(features: dict) -> dict:
    """
    Run classification model.
    Returns quality label, confidence, and probabilities per class.
    """
    if not registry.loaded:
        raise RuntimeError("Models not loaded. Call registry.load() at startup.")

    X = build_feature_vector(features, registry.clf_features)

    pred_enc  = registry.clf_model.predict(X)[0]
    quality   = registry.label_encoder.inverse_transform([pred_enc])[0]
    probas    = registry.clf_model.predict_proba(X)[0]
    classes   = registry.label_encoder.classes_

    proba_dict   = {cls: round(float(p) * 100, 1)
                    for cls, p in zip(classes, probas)}
    confidence   = round(float(max(probas)) * 100, 1)

    return {
        "quality"        : quality,
        "confidence"     : confidence,
        "probabilities"  : proba_dict,
        "model_accuracy" : f"{registry.clf_accuracy * 100:.1f}%",
    }


def run_regression(features: dict) -> dict:
    """
    Run regression model.
    Returns predicted rank and honest interpretation.
    """
    if not registry.reg_model:
        return {
            "predicted_rank" : None,
            "available"      : False,
            "reason"         : "Regression model not loaded",
        }

    X = build_feature_vector(features, registry.reg_features)

    pred_log  = registry.reg_model.predict(X)[0]
    pred_rank = int(np.clip(np.round(np.expm1(pred_log)), 1, 30))

    # Honest interpretation
    if pred_rank <= 10:
        tier = "top 10"
    elif pred_rank <= 20:
        tier = "positions 11–20"
    else:
        tier = "positions 21–30"

    return {
        "predicted_rank" : pred_rank,
        "tier"           : tier,
        "available"      : True,
        "r2_score"       : registry.reg_r2,
        "disclaimer"     : (
            f"Predicted rank: ~{pred_rank} ({tier}). "
            f"Note: rank prediction has R²={registry.reg_r2:.2f} — "
            "on-page features explain limited ranking variance. "
            "Off-page factors (backlinks, domain authority) "
            "are the primary ranking drivers."
        ),
    }


def predict(features: dict, lighthouse_available: bool) -> dict:
    """
    Full prediction pipeline.
    Runs classification + regression and returns combined result.
    """
    # Determine which model accuracy to report
    if not lighthouse_available:
        accuracy_note = (
            "46.5% accuracy — Lighthouse score unavailable. "
            "Deploy page or use public URL for full 83.8% analysis."
        )
    else:
        accuracy_note = "83.8% accuracy — full feature set"

    clf_result = run_classification(features)
    reg_result = run_regression(features)

    return {
        "classification" : clf_result,
        "regression"     : reg_result,
        "accuracy_note"  : accuracy_note,
        "features_used"  : len(registry.clf_features),
    }
