"""
services/serp_competitor.py
============================
SERP competitor pipeline for computing query-relative z-score and
percentile features (*_vs_query_z, *_vs_query_pct).

Workflow:
  1. fetch_serp_urls(keyword)
       → calls Serper.dev API, returns top N organic result URLs

  2. scrape_competitors(urls, keyword)
       → scrapes all URLs in parallel, extracts full feature set for each
       → fetches OPR + CC signals for each competitor domain
       → returns list of feature dicts

  3. compute_query_relative_features(target, competitors)
       → for each of the 17 base metrics, computes:
             z-score    = (target - mean) / std   (0 if std==0)
             percentile = fraction of competitors strictly below target * 100
       → returns dict with 34 *_vs_query_z and *_vs_query_pct entries

All scraping is done with asyncio to run competitors in parallel.
"""

import asyncio
import logging
import statistics
import httpx
from typing import Optional

from config import (
    SERPER_API_KEY, SERPER_ENDPOINT, SERPER_NUM_RESULTS,
    SERP_SCRAPE_TIMEOUT, SERP_MAX_CONCURRENT,
    SCRAPER_CONNECT_TIMEOUT,
)
from services.feature_extractor import extract_features_from_html
from services.cc_graph import fetch_cc_signals

logger = logging.getLogger(__name__)

# The 17 features that get both a z-score and a percentile comparison
COMPARISON_FEATURES = [
    "cc_referring_domains_log",
    "cc_pagerank",
    "cc_harmonic_centrality",
    "opr_page_rank",
    "opr_rank_log",
    "domain_frequency_log",
    "lighthouse_seo_score",
    "technical_score",
    "tfidf_relevance",
    "word_count",
    "internal_link_count",
    "keyword_density",
    "keyword_frequency",
    "keyword_proximity_score",
    "keyword_prominence_score",
    "semantic_relevance",
    "keyword_variations_count",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ═══════════════════════════════════════════════════════════════════
# SERP URL FETCHING
# ═══════════════════════════════════════════════════════════════════

async def fetch_serp_urls(keyword: str, n: int = SERPER_NUM_RESULTS) -> list[str]:
    """
    Fetch top N organic Google results for a keyword via Serper.dev API.
    Returns a list of URLs (may be shorter than N if fewer organic results).
    Returns [] on API failure.
    """
    payload = {"q": keyword, "num": n, "gl": "us", "hl": "en"}
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(SERPER_ENDPOINT, json=payload, headers=headers)

        if resp.status_code != 200:
            logger.warning(f"Serper API error: status {resp.status_code} for '{keyword}'")
            return []

        data    = resp.json()
        organic = data.get("organic", [])
        urls    = [item["link"] for item in organic if "link" in item]
        logger.info(f"Serper: {len(urls)} competitor URLs for '{keyword}'")
        return urls[:n]

    except Exception as e:
        logger.error(f"Serper fetch error for '{keyword}': {e}")
        return []


# ═══════════════════════════════════════════════════════════════════
# SINGLE COMPETITOR SCRAPE
# ═══════════════════════════════════════════════════════════════════

async def _scrape_one_competitor(
    url: str,
    keyword: str,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """
    Scrape a single competitor URL: fetch HTML, extract features, fetch CC + OPR.
    Returns feature dict or None on failure.
    """
    async with semaphore:
        # ── Fetch HTML ─────────────────────────────────────────
        try:
            async with httpx.AsyncClient(
                timeout=SERP_SCRAPE_TIMEOUT,
                follow_redirects=True,
                headers=HEADERS,
            ) as client:
                resp = await client.get(url)

            if resp.status_code != 200:
                logger.debug(f"Competitor skip (HTTP {resp.status_code}): {url}")
                return None

            html = resp.text

        except Exception as e:
            logger.debug(f"Competitor scrape failed ({e}): {url}")
            return None

        # ── Extract features (CPU-bound — run in thread pool) ──
        try:
            loop = asyncio.get_event_loop()
            features = await loop.run_in_executor(
                None, extract_features_from_html, html, url, keyword
            )
        except Exception as e:
            logger.debug(f"Competitor feature extraction failed ({e}): {url}")
            return None

        # ── CC signals ─────────────────────────────────────────
        domain = features.get("domain", "")
        if domain:
            try:
                cc = await fetch_cc_signals(domain)
                features.update(cc)
            except Exception as e:
                logger.debug(f"CC signal error for {domain}: {e}")

        # ── OPR signals ────────────────────────────────────────
        # Imported here to avoid circular imports; OPR is async
        if domain:
            try:
                from services.external_apis import fetch_opr_score
                opr = await fetch_opr_score(domain)
                features["opr_page_rank"]    = opr.get("opr_page_rank", 0.0)
                features["opr_rank_log"]     = opr.get("opr_rank_log", 0.0)
                features["opr_domain_found"] = opr.get("opr_domain_found", 0)
            except Exception as e:
                logger.debug(f"OPR error for {domain}: {e}")

        # lighthouse_seo_score stays at -1 for competitors (too slow to fetch for all)
        # We use -1 as a sentinel so the z-score computation can skip it if needed.
        # In practice, the regressor was likely trained with -1 meaning "unavailable".
        features["lighthouse_seo_score"] = features.get("lighthouse_seo_score", -1)

        logger.debug(f"Competitor OK: {url} ({features.get('word_count', 0)} words)")
        return features


# ═══════════════════════════════════════════════════════════════════
# PARALLEL COMPETITOR SCRAPING
# ═══════════════════════════════════════════════════════════════════

async def scrape_competitors(
    urls: list[str],
    keyword: str,
    max_concurrent: int = SERP_MAX_CONCURRENT,
) -> list[dict]:
    """
    Scrape all competitor URLs in parallel with a concurrency limit.
    Returns list of successfully extracted feature dicts.
    """
    if not urls:
        return []

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks     = [_scrape_one_competitor(url, keyword, semaphore) for url in urls]
    results   = await asyncio.gather(*tasks, return_exceptions=True)

    competitors = []
    for r in results:
        if isinstance(r, dict):
            competitors.append(r)
        # None and exceptions are skipped (already logged)

    logger.info(f"Competitors scraped: {len(competitors)}/{len(urls)} succeeded")
    return competitors


# ═══════════════════════════════════════════════════════════════════
# QUERY-RELATIVE Z-SCORE + PERCENTILE COMPUTATION
# ═══════════════════════════════════════════════════════════════════

def _safe_float(val, default: float = 0.0) -> float:
    """Convert a feature value to float safely."""
    if val is None:
        return default
    try:
        f = float(val)
        return f if (f == f and abs(f) < 1e15) else default  # NaN / inf guard
    except (ValueError, TypeError):
        return default


def compute_query_relative_features(
    target: dict,
    competitors: list[dict],
) -> dict:
    """
    Compute z-score and percentile features comparing target to competitors.

    For each feature in COMPARISON_FEATURES:
      *_vs_query_z   = (target - mean(competitors)) / std(competitors)
                       → 0.0 if std == 0 or no competitors
      *_vs_query_pct = percentile of target among competitors (0–100)
                       → 50.0 if no competitors

    lighthouse_seo_score is handled specially: -1 means "unavailable".
    If target or all competitors have -1, the z-score and percentile are 0.

    Returns dict of 34 features (17 z-score + 17 percentile).
    """
    result = {}

    if not competitors:
        # No competitor data — default all to 0
        for feat in COMPARISON_FEATURES:
            result[f"{feat}_vs_query_z"]   = 0.0
            result[f"{feat}_vs_query_pct"] = 0.0
        return result

    for feat in COMPARISON_FEATURES:
        target_val = _safe_float(target.get(feat, 0))

        # Collect competitor values; skip -1 sentinels for lighthouse
        comp_vals = []
        for c in competitors:
            v = _safe_float(c.get(feat, 0))
            if feat == "lighthouse_seo_score" and v < 0:
                continue
            comp_vals.append(v)

        # Also skip target if lighthouse is unavailable
        if feat == "lighthouse_seo_score" and target_val < 0:
            result[f"{feat}_vs_query_z"]   = 0.0
            result[f"{feat}_vs_query_pct"] = 0.0
            continue

        if not comp_vals:
            result[f"{feat}_vs_query_z"]   = 0.0
            result[f"{feat}_vs_query_pct"] = 0.0
            continue

        # ── Z-score ───────────────────────────────────────────
        mean = statistics.mean(comp_vals)
        std  = statistics.pstdev(comp_vals)  # population std (same as training)
        if std > 1e-10:
            z = (target_val - mean) / std
        else:
            z = 0.0

        # Clip to [-5, 5] to prevent extreme values
        z = max(-5.0, min(5.0, z))

        # ── Percentile ────────────────────────────────────────
        n_below = sum(1 for v in comp_vals if v < target_val)
        pct     = (n_below / len(comp_vals)) * 100.0

        result[f"{feat}_vs_query_z"]   = round(z,   6)
        result[f"{feat}_vs_query_pct"] = round(pct, 4)

    return result


# ═══════════════════════════════════════════════════════════════════
# FULL COMPETITOR PIPELINE (convenience entry point)
# ═══════════════════════════════════════════════════════════════════

async def run_competitor_pipeline(
    keyword: str,
    target_url: str = "",
) -> tuple[list[dict], dict]:
    """
    Full pipeline:
      1. Fetch SERP results for keyword
      2. Remove target URL from competitor list (don't compare against itself)
      3. Scrape all competitors in parallel (with OPR + CC per competitor)
      4. Return (competitors, {placeholder z/pct dict with 0s})

    The caller must call compute_query_relative_features(target, competitors)
    AFTER the target features are enriched with its own OPR/CC/Lighthouse,
    since those are needed for the target_val in the z-score formula.
    """
    urls = await fetch_serp_urls(keyword)

    # Remove the target URL if it appears in the SERP results
    if target_url:
        urls = [u for u in urls if u.rstrip("/") != target_url.rstrip("/")]

    competitors = await scrape_competitors(urls, keyword)
    return competitors
