"""
services/cc_graph.py
====================
Domain-level Common Crawl graph lookups.

Primary source: backend/data/cc_lookup.duckdb
  Built once from cc_graph.parquet via scripts/build_cc_lookup.py.
  Table: domain_metrics (domain, in_degree, referring_domains_log, pagerank, harmonic_centrality)
  Sorted by domain — DuckDB zone-map pruning gives ~25ms per lookup without a RAM-heavy index.

Fallback chain:
  1. cc_lookup.duckdb  (fast, ~25ms)
  2. cc_graph.parquet  (slow, ~10s — only if DuckDB file is missing)
  3. CDX HTTP API      (cc_found only — when both local files are absent)
"""

import logging
import threading
import httpx
from config import CC_DUCKDB_PATH, CC_PARQUET_PATH, CC_CDX_ENDPOINT, CC_CDX_TIMEOUT

logger = logging.getLogger(__name__)

try:
    import duckdb as _duckdb
    _DUCKDB_OK = True
except ImportError:
    _DUCKDB_OK = False
    logger.warning("duckdb not installed — CC lookups disabled. Run: pip install duckdb")

# One DuckDB connection per OS thread (connections are not thread-safe).
# FastAPI's sync code runs in a threadpool, so each worker gets its own.
_thread_local = threading.local()

_DEFAULTS = {
    "cc_found"                : 0,
    "cc_pagerank"             : 0.0,
    "cc_harmonic_centrality"  : 0.0,
    "cc_referring_domains_log": 0.0,
}

_SELECT_DUCKDB = """
    SELECT pagerank, harmonic_centrality, referring_domains_log
    FROM domain_metrics
    WHERE domain = ?
    LIMIT 1
"""

_SELECT_PARQUET = """
    SELECT pagerank, harmonic_centrality, referring_domains_log
    FROM cc_graph
    WHERE domain = ?
    LIMIT 1
"""


def _get_con():
    """
    Return a thread-local DuckDB connection, warmed up and ready.

    Priority:
      1. cc_lookup.duckdb  — persistent file, sorted table, ~25ms lookups
      2. cc_graph.parquet  — raw archive, ~10s lookups (last resort)
    """
    con = getattr(_thread_local, "con", None)
    if con is not None:
        return con

    if not _DUCKDB_OK:
        return None

    # ── Primary: cc_lookup.duckdb ─────────────────────────────────────────────
    if CC_DUCKDB_PATH.exists():
        try:
            con = _duckdb.connect(str(CC_DUCKDB_PATH), read_only=True)
            # Warmup: load file metadata + zone maps so the first real query is fast
            con.execute("SELECT COUNT(*) FROM domain_metrics LIMIT 1").fetchone()
            _thread_local.con = con
            _thread_local.use_parquet = False
            logger.info(
                f"CC graph DuckDB ready [{threading.current_thread().name}]: {CC_DUCKDB_PATH}"
            )
            return con
        except Exception as e:
            logger.error(f"Failed to open cc_lookup.duckdb: {e}")

    # ── Fallback: raw parquet ─────────────────────────────────────────────────
    if CC_PARQUET_PATH.exists():
        try:
            con = _duckdb.connect()
            con.execute(
                f"CREATE VIEW cc_graph AS SELECT * FROM read_parquet('{CC_PARQUET_PATH}')"
            )
            con.execute("SELECT domain FROM cc_graph LIMIT 1").fetchone()
            _thread_local.con = con
            _thread_local.use_parquet = True
            logger.warning(
                f"cc_lookup.duckdb not found — falling back to slow parquet: {CC_PARQUET_PATH}"
            )
            return con
        except Exception as e:
            logger.error(f"Failed to open cc_graph.parquet: {e}")

    logger.warning("No CC graph data source found. Domain authority signals will be 0.")
    return None


def lookup_domain(domain: str) -> dict:
    """
    Look up a domain in the CC graph.

    Returns:
        {
            cc_found                : int   (1 or 0)
            cc_pagerank             : float
            cc_harmonic_centrality  : float
            cc_referring_domains_log: float  log(referring_domains + 1)
        }
    """
    con = _get_con()
    if con is None:
        return dict(_DEFAULTS)

    use_parquet = getattr(_thread_local, "use_parquet", False)
    sql = _SELECT_PARQUET if use_parquet else _SELECT_DUCKDB

    # Try bare domain, then www variant, then strip www
    candidates = [domain]
    if domain.startswith("www."):
        candidates.append(domain[4:])
    else:
        candidates.append(f"www.{domain}")

    for candidate in candidates:
        try:
            row = con.execute(sql, [candidate]).fetchone()
            if row:
                pr, hc, ref_log = row
                return {
                    "cc_found"                : 1,
                    "cc_pagerank"             : float(pr  or 0.0),
                    "cc_harmonic_centrality"  : float(hc  or 0.0),
                    "cc_referring_domains_log": float(ref_log or 0.0),
                }
        except Exception as e:
            logger.error(f"CC lookup error for {candidate!r}: {e}")
            # Reset thread-local so next call retries the connection
            _thread_local.con = None

    return dict(_DEFAULTS)


async def fetch_cc_signals(domain: str) -> dict:
    """
    Async entry point for CC graph signals.

    1. Fast path  : local DuckDB / Parquet lookup (sync, run in threadpool by caller).
    2. Slow path  : CDX HTTP API fallback when no local file exists.
    """
    if CC_DUCKDB_PATH.exists() or CC_PARQUET_PATH.exists():
        return lookup_domain(domain)
    return await _cdx_fallback(domain)


async def _cdx_fallback(domain: str) -> dict:
    """CC CDX HTTP fallback — returns cc_found only; PR/HC remain 0."""
    result = dict(_DEFAULTS)
    params = {"url": f"*.{domain}", "output": "json", "limit": 1, "fl": "urlkey"}
    try:
        async with httpx.AsyncClient(timeout=CC_CDX_TIMEOUT) as client:
            resp = await client.get(CC_CDX_ENDPOINT, params=params)
        if resp.status_code == 200 and resp.text.strip():
            result["cc_found"] = 1
        else:
            logger.debug(f"CC CDX: {domain} not found (status {resp.status_code})")
    except Exception as e:
        logger.warning(f"CC CDX fallback error for {domain}: {e}")
    return result
