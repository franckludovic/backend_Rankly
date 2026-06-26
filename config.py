"""
config.py
=========
All settings in one place.
Change model paths here when you get better models- nothing else changes.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Base paths ────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
MODELS_DIR = BASE_DIR / "models"
DATA_DIR   = BASE_DIR / "data"          # for CC graph SQLite DB

# ── Model paths- SWAP THESE when you get better models ──────────
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
SERPER_API_KEY      = os.getenv("SERPER_API_KEY", "YOUR_SERPER_KEY_HERE")
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY")
RESEND_API_KEY      = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL   = os.getenv("RESEND_FROM_EMAIL", "Rankly <alerts@rankly.app>")

# ── Lemon Squeezy ─────────────────────────────────────────────
LS_API_KEY         = os.getenv("LS_API_KEY", "")
LS_STORE_ID        = os.getenv("LS_STORE_ID", "")
LS_WEBHOOK_SECRET  = os.getenv("LS_WEBHOOK_SECRET", "")
LS_VARIANT_PRO        = os.getenv("LS_VARIANT_PRO", "")        # variant ID from LS dashboard
LS_VARIANT_AGENCY     = os.getenv("LS_VARIANT_AGENCY", "")
LS_VARIANT_BUSINESS   = os.getenv("LS_VARIANT_BUSINESS", "")
LS_VARIANT_DEV_ADDON  = os.getenv("LS_VARIANT_DEV_ADDON", "")  # Pro add-on: unlocks API key access
APP_BASE_URL       = os.getenv("APP_BASE_URL", "http://localhost:5173")

# ── Lighthouse settings ───────────────────────────────────────────
LIGHTHOUSE_STRATEGY = "mobile"          # mobile-first indexing
LIGHTHOUSE_TIMEOUT  = 30               # seconds

# ── Scraper settings ──────────────────────────────────────────────
SCRAPER_CONNECT_TIMEOUT = 6
SCRAPER_READ_TIMEOUT    = 20
SCRAPER_MIN_WORD_COUNT  = 50

# ── SERP competitor settings ──────────────────────────────────────
SERPER_NUM_RESULTS       = 10           # top N SERP results to use as competitors
SERPER_ENDPOINT          = "https://google.serper.dev/search"
SERP_SCRAPE_TIMEOUT      = 10          # per-competitor scrape timeout (seconds)
SERP_MAX_CONCURRENT      = 10          # max parallel competitor scrapes

# ── Common Crawl web graph ────────────────────────────────────────
# Pre-computed domain-level PageRank + Harmonic Centrality + referring domains.
# cc_lookup.duckdb is built once from cc_graph.parquet via scripts/build_cc_lookup.py.
# The parquet is kept as the source archive for future rebuilds.
CC_DUCKDB_PATH  = DATA_DIR / "cc_lookup.duckdb"   # Fast sorted DuckDB (primary)
CC_PARQUET_PATH = DATA_DIR / "cc_graph.parquet"   # Archive / source for rebuilds
# CDX API fallback: used for cc_found check when both files are absent
CC_CDX_ENDPOINT = "https://index.commoncrawl.org/CC-MAIN-2024-10-index"
CC_CDX_TIMEOUT  = 8                               # seconds

# ── CORS ────────────────────────────────────────────────────────
# Localhost defaults for dev; production origins come from the
# ALLOWED_ORIGINS env var (comma-separated), e.g.
#   ALLOWED_ORIGINS="https://rankly.pages.dev,https://rankly.app"
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
]
_extra_origins = os.getenv("ALLOWED_ORIGINS", "")
if _extra_origins:
    ALLOWED_ORIGINS += [o.strip() for o in _extra_origins.split(",") if o.strip()]

# ── Server ────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 8000

RELOAD_SECRET = os.getenv("RELOAD_SECRET", "change-this-secret")

# ── Supabase settings ─────────────────────────────────────────────
SUPABASE_URL        = os.getenv("SUPABASE_URL", os.getenv("VITE_SUPABASE_URL"))
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

