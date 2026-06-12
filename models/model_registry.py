"""
models/model_registry.py
========================
Loads all models once at startup and keeps them in memory.

TO SWAP MODELS:
  1. Replace the .joblib files in models/ folder
  2. Restart the server (or call reload_models() endpoint)
  That's it — no code changes needed anywhere else.
"""

import joblib
import logging
from pathlib import Path
from config import (
    CLF_MODEL_PATH, REG_MODEL_PATH,
    LABEL_ENCODER_PATH, CLF_FEATURES_PATH, REG_FEATURES_PATH
)

logger = logging.getLogger(__name__)


class ModelRegistry:
    """
    Holds all ML models in memory.
    Loaded once at startup — fast inference, no disk reads per request.
    """

    def __init__(self):
        self.clf_model    = None
        self.reg_model    = None
        self.label_encoder= None
        self.clf_features = None
        self.reg_features = None
        self.loaded       = False
        self.clf_accuracy = 0.838   # update when you get better models
        self.reg_r2       = -0.19   # update when you get better models

    def load(self):
        """Load all models from disk. Called once at startup."""
        logger.info("Loading models...")

        try:
            self.clf_model     = joblib.load(CLF_MODEL_PATH)
            logger.info(f"✓ Classification model loaded: {CLF_MODEL_PATH.name}")
        except FileNotFoundError:
            raise RuntimeError(
                f"Classification model not found at {CLF_MODEL_PATH}. "
                "Copy your xgb_classifier.joblib to the models/ folder."
            )

        try:
            self.reg_model     = joblib.load(REG_MODEL_PATH)
            logger.info(f"✓ Regression model loaded: {REG_MODEL_PATH.name}")
        except FileNotFoundError:
            logger.warning(
                f"Regression model not found at {REG_MODEL_PATH}. "
                "Rank prediction will be unavailable."
            )

        self.label_encoder = joblib.load(LABEL_ENCODER_PATH)
        self.clf_features  = joblib.load(CLF_FEATURES_PATH)
        self.reg_features  = joblib.load(REG_FEATURES_PATH)
        self.loaded        = True

        logger.info(
            f"Models ready — "
            f"clf_features={len(self.clf_features)}  "
            f"reg_features={len(self.reg_features) if self.reg_features else 0}"
        )

    def reload(self):
        """Reload models from disk (call after swapping .joblib files)."""
        self.loaded = False
        self.load()
        logger.info("Models reloaded successfully")

    def status(self) -> dict:
        return {
            "loaded"              : self.loaded,
            "clf_model"           : str(CLF_MODEL_PATH.name),
            "reg_model"           : str(REG_MODEL_PATH.name) if self.reg_model else None,
            "clf_features_count"  : len(self.clf_features) if self.clf_features else 0,
            "reg_features_count"  : len(self.reg_features) if self.reg_features else 0,
            "clf_accuracy"        : f"{self.clf_accuracy * 100:.1f}%",
            "reg_r2"              : self.reg_r2,
        }


# Singleton — shared across all requests
registry = ModelRegistry()
