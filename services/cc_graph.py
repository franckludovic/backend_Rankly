"""
services/cc_graph.py
====================
Fast O(1) domain-level Common Crawl graph lookups from local SQLite database.

The database is built ONCE by running:
    python scripts/download_cc_graph.py

At inference time, lookups are microsecond-speed (SQLite with index).

Fallback behaviour:
  - If the SQLite DB doesn't exist: falls back to the CC CDX API (live HTTP call)
    to at least populate cc_found. PageRank/HC remain 0.
  - If a domain is not in the DB: all values default to 0, cc_found = 0.
"""

import asyncio
import sqlite3
import logging
import math
import httpx
from pathlib import Path
from config import CC_DB_PATH, CC_CDX_ENDPOINT, CC_CDX_TIMEOUT

logger = logging.getLogger(__name__)

# ── Module-level SQLite connection (read-only, persistent) ────────
_con: sqlite3.Connection | None = None


def _get_connection() -> sqlite3.Connection | None:
    """Return a cached read-only SQLite connection, or None if DB missing."""
    global _con
    if _con is not None:
        return _con
    if not CC_DB_PATH.exists():
        logger.warning(
            f"CC graph DB not found at {CC_DB_PATH}. "
            "Run scripts/download_cc_graph.py to build it. "
            "Falling back to CDX API for cc_found only."
        )
        return None
    try:
        _con = sqlite3.connect(f"file:{CC_DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        _con.row_factory = sqlite3.Row
        count = _con.execute("SELECT COUNT(*) FROM cc_domains").fetchone()[0]
        logger.info(f"CC graph DB loaded: {count:,} domains at {CC_DB_PATH}")
        return _con
    except Exception as e:
        logger.error(f"Failed to open CC graph DB: {e}")
        return None


def lookup_domain(domain: str) -> dict:
    """
    Look up a domain in the local CC graph SQLite database.

    Returns:
        {
            cc_found                : int   1 or 0
            cc_pagerank             : float
            cc_harmonic_centrality  : float
            cc_referring_domains_log: float   log1p(in_degree)
        }
    """
    defaults = {
        "cc_found"                : 0,
        "cc_pagerank"             : 0.0,
        "cc_harmonic_centrality"  : 0.0,
        "cc_referring_domains_log": 0.0,
    }

    con = _get_connection()
    if con is None:
        return defaults

    # Try eTLD+1 first, then with/without www
    candidates = [domain]
    if domain.startswith("www."):
        candidates.append(domain[4:])
    else:
        candidates.append(f"www.{domain}")

    for candidate in candidates:
        try:
            row = con.execute(
                "SELECT pagerank, harmonic_centrality, in_degree, referring_domains_log "
                "FROM cc_domains WHERE domain = ?",
                (candidate,)
            ).fetchone()
            if row:
                return {
                    "cc_found"                : 1,
                    "cc_pagerank"             : float(row["pagerank"]),
                    "cc_harmonic_centrality"  : float(row["harmonic_centrality"]),
                    "cc_referring_domains_log": float(row["referring_domains_log"]),
                }
        except Exception as e:
            logger.error(f"CC DB lookup error for {candidate}: {e}")

    return defaults


async def fetch_cc_signals(domain: str) -> dict:
    """
    Async entry point for CC graph signals.

    1. Try local SQLite lookup (microseconds).
    2. If DB absent, fall back to live CC CDX API for cc_found
       (PageRank and HC remain 0 — can't compute from CDX alone).
    """
    # Fast path: local DB
    if CC_DB_PATH.exists():
        return lookup_domain(domain)

    # Slow path: CDX API fallback
    return await _cdx_fallback(domain)


async def _cdx_fallback(domain: str) -> dict:
    """
    Query the CC CDX index API to check if a domain is crawled.
    Returns cc_found=1/0 only; other metrics remain 0.
    """
    defaults = {
        "cc_found"                : 0,
        "cc_pagerank"             : 0.0,
        "cc_harmonic_centrality"  : 0.0,
        "cc_referring_domains_log": 0.0,
    }

    url    = CC_CDX_ENDPOINT
    params = {
        "url"    : f"*.{domain}",
        "output" : "json",
        "limit"  : 1,
        "fl"     : "urlkey",
    }

    try:
        async with httpx.AsyncClient(timeout=CC_CDX_TIMEOUT) as client:
            resp = await client.get(url, params=params)
        if resp.status_code == 200 and resp.text.strip():
            defaults["cc_found"] = 1
            logger.debug(f"CC CDX: {domain} found in crawl index")
        else:
            logger.debug(f"CC CDX: {domain} not found (status {resp.status_code})")
    except Exception as e:
        logger.warning(f"CC CDX fallback error for {domain}: {e}")

    return defaults
