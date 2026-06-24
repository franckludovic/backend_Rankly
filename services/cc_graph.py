"""
services/cc_graph.py
====================
Domain-level Common Crawl graph lookups from a local Parquet file.

SETUP:
    Drop  backend/data/cc_graph.parquet  into place- that's it.
    The file is queried with DuckDB (predicate-pushdown, no full scan).

    For best performance the Parquet should be sorted by `domain` so
    DuckDB's row-group min/max statistics can skip most of the file.
    A single lookup then reads 1-2 row groups out of thousands.

FALLBACK:
    If the file is absent, the CDX HTTP API is used to populate
    cc_found=1/0.  PageRank and HC remain 0 (can't be derived from CDX).
"""

import logging
import threading
import httpx
from config import CC_PARQUET_PATH, CC_CDX_ENDPOINT, CC_CDX_TIMEOUT

logger = logging.getLogger(__name__)

try:
    import duckdb as _duckdb
    _DUCKDB_OK = True
except ImportError:
    _DUCKDB_OK = False
    logger.warning("duckdb not installed- CC Parquet lookups disabled. Run: pip install duckdb")

# One DuckDB connection per OS thread (connections are not thread-safe).
# FastAPI's sync code runs in threadpool workers, so each worker gets its own.
_thread_local = threading.local()

_PARQUET = str(CC_PARQUET_PATH)

_SELECT = """
    SELECT pagerank, harmonic_centrality, referring_domains_log
    FROM cc_graph
    WHERE domain = ?
    LIMIT 1
"""

_DEFAULTS = {
    "cc_found"                : 0,
    "cc_pagerank"             : 0.0,
    "cc_harmonic_centrality"  : 0.0,
    "cc_referring_domains_log": 0.0,
}


def _get_con():
    """Return a thread-local DuckDB connection with cc_graph view registered."""
    con = getattr(_thread_local, "con", None)
    if con is not None:
        return con

    if not _DUCKDB_OK:
        return None

    if not CC_PARQUET_PATH.exists():
        logger.warning(
            f"CC graph Parquet not found at {CC_PARQUET_PATH}. "
            "Falling back to CDX API (cc_found only)."
        )
        return None

    try:
        con = _duckdb.connect()
        # Register a view so the schema is parsed once per connection lifetime.
        # DuckDB will reuse row-group metadata across queries on the same connection.
        con.execute(f"CREATE VIEW cc_graph AS SELECT * FROM read_parquet('{_PARQUET}')")
        # Quick sanity check (reads one row only)
        con.execute("SELECT domain FROM cc_graph LIMIT 1").fetchone()
        _thread_local.con = con
        logger.info(f"CC graph Parquet ready [{threading.current_thread().name}]: {CC_PARQUET_PATH}")
        return con
    except Exception as e:
        logger.error(f"Failed to open CC graph Parquet: {e}")
        return None


def lookup_domain(domain: str) -> dict:
    """
    Look up a domain in the local CC graph Parquet file.

    Returns:
        {
            cc_found                : int   1 or 0
            cc_pagerank             : float
            cc_harmonic_centrality  : float
            cc_referring_domains_log: float   log1p(in_degree)
        }
    """
    con = _get_con()
    if con is None:
        return dict(_DEFAULTS)

    # Try eTLD+1, then www variant
    candidates = [domain]
    if domain.startswith("www."):
        candidates.append(domain[4:])
    else:
        candidates.append(f"www.{domain}")

    for candidate in candidates:
        try:
            row = con.execute(_SELECT, [candidate]).fetchone()
            if row:
                pr, hc, ref_log = row
                return {
                    "cc_found"                : 1,
                    "cc_pagerank"             : float(pr),
                    "cc_harmonic_centrality"  : float(hc),
                    "cc_referring_domains_log": float(ref_log),
                }
        except Exception as e:
            logger.error(f"CC Parquet lookup error for {candidate}: {e}")

    return dict(_DEFAULTS)


async def fetch_cc_signals(domain: str) -> dict:
    """
    Async entry point for CC graph signals.

    1. Fast path: local Parquet lookup via DuckDB.
    2. Slow path: CDX API fallback if file is absent.
    """
    if CC_PARQUET_PATH.exists():
        return lookup_domain(domain)
    return await _cdx_fallback(domain)


async def _cdx_fallback(domain: str) -> dict:
    """CC CDX HTTP fallback- returns cc_found only; PR/HC remain 0."""
    result = dict(_DEFAULTS)
    params = {"url": f"*.{domain}", "output": "json", "limit": 1, "fl": "urlkey"}
    try:
        async with httpx.AsyncClient(timeout=CC_CDX_TIMEOUT) as client:
            resp = await client.get(CC_CDX_ENDPOINT, params=params)
        if resp.status_code == 200 and resp.text.strip():
            result["cc_found"] = 1
            logger.debug(f"CC CDX: {domain} found")
        else:
            logger.debug(f"CC CDX: {domain} not found (status {resp.status_code})")
    except Exception as e:
        logger.warning(f"CC CDX fallback error for {domain}: {e}")
    return result
