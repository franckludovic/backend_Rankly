"""
services/serp_cache.py
======================
Handles local SQLite caching of competitor SERP features mapped to keywords.
Cuts down response latency and avoids repetitive external scraping.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Base path matching backend structure
BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "serp_cache.db"


def _init_db():
    """Ensure data directory and SQLite table exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS serp_cache (
                    keyword       TEXT PRIMARY KEY,
                    competitors   TEXT NOT NULL,
                    serp_features TEXT NOT NULL DEFAULT '[]',
                    updated_at    TEXT NOT NULL
                )
            """)
            # Migrate existing tables that predate the serp_features column
            try:
                conn.execute("ALTER TABLE serp_cache ADD COLUMN serp_features TEXT NOT NULL DEFAULT '[]'")
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to initialize SERP cache database: {e}")


# Initialize table on import
_init_db()


def get_cached_competitors(keyword: str, ttl_hours: int = 48) -> list[dict] | None:
    """
    Retrieve cached competitor feature dictionaries for a keyword.
    Returns None if cache is expired, invalid, or missing.
    """
    keyword_clean = keyword.strip().lower()
    if not keyword_clean:
        return None

    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT competitors, updated_at FROM serp_cache WHERE keyword = ?",
                (keyword_clean,)
            ).fetchone()

            if not row:
                return None

            # Parse and check TTL
            updated_at_str = row["updated_at"]
            try:
                updated_at = datetime.fromisoformat(updated_at_str)
            except ValueError:
                # If timestamp is corrupt, treat as cache miss
                return None

            if (datetime.utcnow() - updated_at) > timedelta(hours=ttl_hours):
                logger.info(f"Cache expired (TTL {ttl_hours}h) for keyword: '{keyword_clean}'")
                return None

            competitors = json.loads(row["competitors"])
            logger.info(f"Cache hit for keyword: '{keyword_clean}'")
            return competitors

    except Exception as e:
        logger.error(f"Error reading SERP cache for '{keyword_clean}': {e}")
        return None


def set_cached_competitors(keyword: str, competitors: list[dict]) -> None:
    """Store competitor feature dicts for a keyword. Preserves serp_features if already set."""
    keyword_clean = keyword.strip().lower()
    if not keyword_clean or not competitors:
        return
    try:
        serialized = json.dumps(competitors)
        now_str    = datetime.utcnow().isoformat()
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO serp_cache (keyword, competitors, updated_at) VALUES (?, ?, ?)",
                (keyword_clean, serialized, now_str)
            )
            conn.commit()
            logger.info(f"Cached {len(competitors)} competitors for keyword: '{keyword_clean}'")
    except Exception as e:
        logger.error(f"Error writing SERP cache for '{keyword_clean}': {e}")


def get_cached_serp_features(keyword: str, ttl_hours: int = 48) -> list[dict] | None:
    """Return cached SERP feature list, or None on miss/expiry."""
    keyword_clean = keyword.strip().lower()
    if not keyword_clean:
        return None
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT serp_features, updated_at FROM serp_cache WHERE keyword = ?",
                (keyword_clean,)
            ).fetchone()
            if not row:
                return None
            try:
                updated_at = datetime.fromisoformat(row["updated_at"])
            except ValueError:
                return None
            if (datetime.utcnow() - updated_at) > timedelta(hours=ttl_hours):
                return None
            return json.loads(row["serp_features"] or "[]")
    except Exception as e:
        logger.error(f"Error reading serp_features cache for '{keyword_clean}': {e}")
        return None


def set_cached_serp_features(keyword: str, serp_features: list[dict]) -> None:
    """Upsert SERP features for a keyword (merges with existing competitors row)."""
    keyword_clean = keyword.strip().lower()
    if not keyword_clean:
        return
    try:
        serialized = json.dumps(serp_features)
        now_str    = datetime.utcnow().isoformat()
        with sqlite3.connect(str(DB_PATH)) as conn:
            # Update if row exists; insert minimal row otherwise
            existing = conn.execute(
                "SELECT keyword FROM serp_cache WHERE keyword = ?", (keyword_clean,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE serp_cache SET serp_features = ?, updated_at = ? WHERE keyword = ?",
                    (serialized, now_str, keyword_clean)
                )
            else:
                conn.execute(
                    "INSERT INTO serp_cache (keyword, competitors, serp_features, updated_at) VALUES (?, '[]', ?, ?)",
                    (keyword_clean, serialized, now_str)
                )
            conn.commit()
    except Exception as e:
        logger.error(f"Error writing serp_features cache for '{keyword_clean}': {e}")
