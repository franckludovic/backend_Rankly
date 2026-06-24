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
from api.routes_audit import router as audit_router
from api.routes_history import router as history_router
from api.routes_roadmap import router as roadmap_router
from api.routes_schedule import router as schedule_router
from api.routes_domain import router as domain_router
from api.routes_bulk import router as bulk_router
from api.routes_competitor_watch import router as competitor_watch_router
from api.routes_developer import router as developer_router
from api.routes_usage import router as usage_router
from api.routes_extension import router as extension_router
from api.routes_billing import router as billing_router
from api.routes_auth import router as auth_router

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
    try:
        from services.scheduler import start_scheduler, stop_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"APScheduler not available (install apscheduler): {e}")
        stop_scheduler = lambda: None
    logger.info("API ready")
    yield
    try:
        stop_scheduler()
    except Exception:
        pass
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
    allow_methods     = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers     = ["*"],
)

# ── Routes ────────────────────────────────────────────────────
app.include_router(router)
app.include_router(audit_router)
app.include_router(history_router)
app.include_router(roadmap_router)
app.include_router(schedule_router)
app.include_router(domain_router)
app.include_router(bulk_router)
app.include_router(competitor_watch_router)
app.include_router(developer_router)
app.include_router(usage_router)
app.include_router(extension_router)
app.include_router(billing_router)
app.include_router(auth_router)

# ── Local run ─────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host   = HOST,
        port   = PORT,
        reload = True,
    )
