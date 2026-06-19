"""
services/external_apis.py
==========================
Lighthouse (PageSpeed Insights), Open PageRank, and Common Crawl API calls.
All async so they run in parallel — saves 10-15 seconds per request.

V6 changes:
  - fetch_opr_score now supports batching multiple domains (for competitors)
  - fetch_cc_signals now delegates to services/cc_graph.py (SQLite lookup + CDX fallback)
  - fetch_all_external_signals includes CC signals alongside Lighthouse + OPR
"""

import asyncio
import logging
import numpy as np
import httpx
from config import LIGHTHOUSE_API_KEY, OPR_API_KEY, LIGHTHOUSE_STRATEGY

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# LIGHTHOUSE
# ═══════════════════════════════════════════════════════════════════

async def fetch_lighthouse_score(url: str) -> dict:
    """
    Fetch Lighthouse SEO score from PageSpeed Insights API.
    Returns dict with score and availability flag.
    Returns score=-1 on failure.
    """
    endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params   = {
        "url"      : url,
        "key"      : LIGHTHOUSE_API_KEY,
        "strategy" : LIGHTHOUSE_STRATEGY,
        "category" : "seo",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(endpoint, params=params)

        if resp.status_code == 400:
            logger.warning(f"Lighthouse: invalid URL {url}")
            return {"score": -1, "available": False, "reason": "invalid_url"}

        if resp.status_code == 429:
            logger.warning("Lighthouse: rate limited")
            return {"score": -1, "available": False, "reason": "rate_limited"}

        if resp.status_code != 200:
            return {"score": -1, "available": False, "reason": f"http_{resp.status_code}"}

        data  = resp.json()
        score = data["lighthouseResult"]["categories"]["seo"]["score"]

        if score is None:
            return {"score": -1, "available": False, "reason": "null_score"}

        score_int = int(round(score * 100))
        return {"score": score_int, "available": True, "reason": "success"}

    except httpx.TimeoutException:
        logger.warning(f"Lighthouse: timeout for {url}")
        return {"score": -1, "available": False, "reason": "timeout"}
    except Exception as e:
        logger.error(f"Lighthouse error: {e}")
        return {"score": -1, "available": False, "reason": "error"}


# ═══════════════════════════════════════════════════════════════════
# OPEN PAGERANK  (single domain)
# ═══════════════════════════════════════════════════════════════════

async def fetch_opr_score(domain: str) -> dict:
    """
    Fetch Open PageRank authority score for a single domain.
    Returns dict with page_rank, rank, found flag.
    """
    results = await fetch_opr_batch([domain])
    return results.get(domain, _opr_defaults())


def _opr_defaults() -> dict:
    return {"opr_page_rank": 0.0, "opr_rank": 0, "opr_rank_log": 0.0, "opr_domain_found": 0}


# ═══════════════════════════════════════════════════════════════════
# OPEN PAGERANK  (batch — up to 100 domains per request)
# ═══════════════════════════════════════════════════════════════════

async def fetch_opr_batch(domains: list[str]) -> dict[str, dict]:
    """
    Fetch Open PageRank for multiple domains in a single API call.
    OPR supports up to 100 domains per request.

    Returns:
        { domain: { opr_page_rank, opr_rank, opr_rank_log, opr_domain_found }, ... }
    """
    if not domains:
        return {}

    endpoint = "https://openpagerank.com/api/v1.0/getPageRank"
    headers  = {"API-OPR": OPR_API_KEY}

    # OPR API uses repeated domains[] params
    params = [("domains[]", d) for d in domains]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(endpoint, headers=headers, params=params)

        if resp.status_code != 200:
            logger.warning(f"OPR batch error: status {resp.status_code}")
            return {d: _opr_defaults() for d in domains}

        data  = resp.json()
        items = data.get("response", [])

        results = {}
        for item in items:
            queried_domain = item.get("domain", "")
            found = item.get("status_code", 404) == 200
            results[queried_domain] = {
                "opr_page_rank"   : float(item.get("page_rank_decimal", 0) or 0),
                "opr_rank"        : int(item.get("rank", 0) or 0),
                "opr_rank_log"    : float(np.log1p(int(item.get("rank", 0) or 0))),
                "opr_domain_found": 1 if found else 0,
            }

        # Fill in defaults for any domain that didn't appear in the response
        for d in domains:
            if d not in results:
                results[d] = _opr_defaults()

        return results

    except Exception as e:
        logger.error(f"OPR batch error: {e}")
        return {d: _opr_defaults() for d in domains}


# ═══════════════════════════════════════════════════════════════════
# COMMON CRAWL (delegates to cc_graph.py)
# ═══════════════════════════════════════════════════════════════════

async def fetch_cc_signals(domain: str) -> dict:
    """
    Fetch CC graph signals for a domain.
    Uses local SQLite lookup (instant) or CDX API fallback.
    """
    from services.cc_graph import fetch_cc_signals as _fetch
    return await _fetch(domain)


# ═══════════════════════════════════════════════════════════════════
# PARALLEL FETCHER — runs Lighthouse + OPR + CC at the same time
# ═══════════════════════════════════════════════════════════════════

async def fetch_all_external_signals(
    url: str,
    domain: str,
    is_local: bool = False,
) -> dict:
    """
    Fetch Lighthouse + OPR + CC signals in parallel for the TARGET URL.

    For local URLs, skips Lighthouse and OPR (but still does CC SQLite lookup
    since that's instant).

    Returns a merged dict of all external signals.
    """
    # CC lookup is always instant (SQLite or CDX fallback) — run regardless
    cc_task = fetch_cc_signals(domain)

    if is_local:
        # Only CC (instant), skip slow external APIs
        cc = await cc_task
        return {
            "lighthouse_score"    : -1,
            "lighthouse_available": False,
            "opr_page_rank"       : 0.0,
            "opr_rank"            : 0,
            "opr_rank_log"        : 0.0,
            "opr_domain_found"    : 0,
            **cc,
        }

    # Run all three in parallel
    lighthouse_task = fetch_lighthouse_score(url)
    opr_task        = fetch_opr_score(domain)

    lighthouse_result, opr_result, cc_result = await asyncio.gather(
        lighthouse_task, opr_task, cc_task,
        return_exceptions=True,
    )

    # Handle any exceptions from gather
    if isinstance(lighthouse_result, Exception):
        logger.warning(f"Lighthouse gather error: {lighthouse_result}")
        lighthouse_result = {"score": -1, "available": False}
    if isinstance(opr_result, Exception):
        logger.warning(f"OPR gather error: {opr_result}")
        opr_result = _opr_defaults()
    if isinstance(cc_result, Exception):
        logger.warning(f"CC gather error: {cc_result}")
        cc_result = {"cc_found": 0, "cc_pagerank": 0.0,
                     "cc_harmonic_centrality": 0.0, "cc_referring_domains_log": 0.0}

    return {
        "lighthouse_score"    : lighthouse_result.get("score", -1),
        "lighthouse_available": lighthouse_result.get("available", False),
        "opr_page_rank"       : opr_result.get("opr_page_rank", 0.0),
        "opr_rank"            : opr_result.get("opr_rank", 0),
        "opr_rank_log"        : opr_result.get("opr_rank_log", 0.0),
        "opr_domain_found"    : opr_result.get("opr_domain_found", 0),
        **cc_result,
    }
