"""
services/external_apis.py
==========================
Lighthouse (PageSpeed Insights) and Open PageRank API calls.
Both are async so they run in parallel — saves 10-15 seconds per request.
"""

import asyncio
import logging
import numpy as np
import httpx   # async HTTP client
from config import LIGHTHOUSE_API_KEY, OPR_API_KEY, LIGHTHOUSE_STRATEGY

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# LIGHTHOUSE
# ═══════════════════════════════════════════════════════════════

async def fetch_lighthouse_score(url: str) -> dict:
    """
    Fetch Lighthouse SEO score from PageSpeed Insights API.
    Returns dict with score and raw audit results.
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
        return {
            "score"    : score_int,
            "available": True,
            "reason"   : "success",
        }

    except httpx.TimeoutException:
        logger.warning(f"Lighthouse: timeout for {url}")
        return {"score": -1, "available": False, "reason": "timeout"}
    except Exception as e:
        logger.error(f"Lighthouse error: {e}")
        return {"score": -1, "available": False, "reason": "error"}


# ═══════════════════════════════════════════════════════════════
# OPEN PAGERANK
# ═══════════════════════════════════════════════════════════════

async def fetch_opr_score(domain: str) -> dict:
    """
    Fetch Open PageRank authority score for a domain.
    Returns dict with page_rank, rank, found flag.
    """
    endpoint = "https://openpagerank.com/api/v1.0/getPageRank"
    headers  = {"API-OPR": OPR_API_KEY}
    params   = [("domains[]", domain)]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(endpoint, headers=headers, params=params)

        if resp.status_code != 200:
            return {"opr_page_rank": 0, "opr_rank": 0, "opr_domain_found": 0}

        data  = resp.json()
        items = data.get("response", [])

        if not items:
            return {"opr_page_rank": 0, "opr_rank": 0, "opr_domain_found": 0}

        item  = items[0]
        found = item.get("status_code", 404) == 200

        return {
            "opr_page_rank"    : float(item.get("page_rank_decimal", 0) or 0),
            "opr_rank"         : int(item.get("rank", 0) or 0),
            "opr_rank_log"     : float(np.log1p(int(item.get("rank", 0) or 0))),
            "opr_domain_found" : 1 if found else 0,
        }

    except Exception as e:
        logger.error(f"OPR error for {domain}: {e}")
        return {"opr_page_rank": 0, "opr_rank": 0,
                "opr_rank_log": 0, "opr_domain_found": 0}


# ═══════════════════════════════════════════════════════════════
# PARALLEL FETCHER — runs both APIs at the same time
# ═══════════════════════════════════════════════════════════════

async def fetch_all_external_signals(
    url: str,
    domain: str,
    is_local: bool = False
) -> dict:
    """
    Fetch Lighthouse + OPR in parallel.
    For local URLs, skips both APIs and returns defaults.

    Without async: 10s (Lighthouse) + 2s (OPR) = 12s sequential
    With async:    max(10s, 2s) = 10s parallel  ← saves 2s per request
    """
    if is_local:
        return {
            "lighthouse_score"  : -1,
            "lighthouse_available": False,
            "opr_page_rank"     : 0,
            "opr_rank"          : 0,
            "opr_rank_log"      : 0,
            "opr_domain_found"  : 0,
        }

    # Run both in parallel
    lighthouse_task = fetch_lighthouse_score(url)
    opr_task        = fetch_opr_score(domain)

    lighthouse_result, opr_result = await asyncio.gather(
        lighthouse_task, opr_task,
        return_exceptions=True
    )

    # Handle exceptions from gather
    if isinstance(lighthouse_result, Exception):
        lighthouse_result = {"score": -1, "available": False}
    if isinstance(opr_result, Exception):
        opr_result = {"opr_page_rank": 0, "opr_rank": 0,
                      "opr_rank_log": 0, "opr_domain_found": 0}

    return {
        "lighthouse_score"    : lighthouse_result.get("score", -1),
        "lighthouse_available": lighthouse_result.get("available", False),
        "opr_page_rank"       : opr_result.get("opr_page_rank", 0),
        "opr_rank"            : opr_result.get("opr_rank", 0),
        "opr_rank_log"        : opr_result.get("opr_rank_log", 0),
        "opr_domain_found"    : opr_result.get("opr_domain_found", 0),
    }
