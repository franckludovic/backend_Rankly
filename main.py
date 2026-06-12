"""
main.py
=======
FastAPI application entry point.

RUN LOCALLY:
    uvicorn main:app --reload --port 8000

RUN ON EC2 (production):
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4

API DOCS (auto-generated):
    http://localhost:8000/docs
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import ALLOWED_ORIGINS, HOST, PORT
from models.model_registry import registry
from api.routes import router

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(name)s] %(levelname)s - %(message)s",
    handlers= [
        logging.StreamHandler(),
        logging.FileHandler("server.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


# ── Startup / Shutdown ────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting SEO Suggestion Engine API...")
    registry.load()
    logger.info("API ready")
    yield
    logger.info("API shutting down")


# ── App ───────────────────────────────────────────────────────
app = FastAPI(
    title       = "SEO Suggestion Engine API",
    description = (
        "ML-powered SEO analysis. "
        "Classification model: 83.8% accuracy. "
        "Analyses any URL or HTML file."
    ),
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ALLOWED_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["GET", "POST"],
    allow_headers     = ["*"],
)

# ── Routes ────────────────────────────────────────────────────
app.include_router(router)

# ── Local run ─────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host   = HOST,
        port   = PORT,
        reload = True,
    )
