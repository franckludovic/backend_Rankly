"""
config.py
=========
All settings in one place.
Change model paths here when you get better models — nothing else changes.
"""

import os
from pathlib import Path

# ── Base paths ────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
MODELS_DIR = BASE_DIR / "models"

# ── Model paths — SWAP THESE when you get better models ──────────
# Just replace the .joblib files in the models/ folder
# and restart the server. Nothing else needs to change.
CLF_MODEL_PATH      = MODELS_DIR / "xgb_classifier_V6.joblib"
REG_MODEL_PATH      = MODELS_DIR / "xgb_regressorV6.joblib"
LABEL_ENCODER_PATH  = MODELS_DIR / "label_encoder_V6.joblib"
CLF_FEATURES_PATH   = MODELS_DIR / "clf_feature_cols_V6.joblib"
REG_FEATURES_PATH   = MODELS_DIR / "reg_feature_colsV6.joblib"
SEMANTIC_MODEL_PATH = MODELS_DIR / "semantic_model"          # all-MiniLM-L6-v2

# ── API Keys ──────────────────────────────────────────────────────
LIGHTHOUSE_API_KEY  = os.getenv("LIGHTHOUSE_API_KEY", "YOUR_KEY_HERE")
OPR_API_KEY         = os.getenv("OPR_API_KEY", "YOUR_OPR_KEY_HERE")

# ── Lighthouse settings ───────────────────────────────────────────
LIGHTHOUSE_STRATEGY = "mobile"          # mobile-first indexing
LIGHTHOUSE_TIMEOUT  = 30               # seconds

# ── Scraper settings ──────────────────────────────────────────────
SCRAPER_CONNECT_TIMEOUT = 6
SCRAPER_READ_TIMEOUT    = 20
SCRAPER_MIN_WORD_COUNT  = 50

# ── CORS — add your extension ID and dashboard URL ───────────────
ALLOWED_ORIGINS = [
    "http://localhost:3000",           # local dashboard dev
    "http://localhost:5173",           # Vite dev server
    "https://your-dashboard.com",      # production dashboard
    # Chrome extension origins are handled separately
]

# ── Server ────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 8000

RELOAD_SECRET = os.getenv("RELOAD_SECRET", "change-this-secret")

