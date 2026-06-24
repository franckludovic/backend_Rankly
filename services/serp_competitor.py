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

def _extract_serp_features(data: dict) -> list[dict]:
    """
    Parse a raw Serper.dev response and return a list of SERP feature opportunity dicts.
    Each dict has: feature, icon, present, traffic_impact, recommendation, (optional) details.
    Called with zero extra API credits- uses the response already fetched for competitor URLs.
    """
    features = []

    if data.get("answerBox"):
        features.append({
            "feature": "Featured Snippet",
            "icon": "snippet",
            "present": True,
            "traffic_impact": "high",
            "recommendation": (
                "Add a direct-answer paragraph immediately after your first H2. "
                "Start with the question phrase, answer concisely in 40–60 words, "
                "then expand with detail below. Use a definition, step list, or table format."
            ),
        })

    paa = data.get("peopleAlsoAsk", [])
    if paa:
        questions = [q.get("question", "") for q in paa if q.get("question")][:4]
        features.append({
            "feature": "People Also Ask",
            "icon": "paa",
            "present": True,
            "traffic_impact": "high",
            "recommendation": (
                "Add an FAQ section answering these exact questions. "
                "Mark it up with FAQPage schema so your answers can appear directly "
                "inside the PAA box without a click."
            ),
            "details": {"questions": questions},
        })

    if data.get("knowledgeGraph"):
        features.append({
            "feature": "Knowledge Panel",
            "icon": "knowledge",
            "present": True,
            "traffic_impact": "medium",
            "recommendation": (
                "This is an entity/brand query. Strengthen your entity signals: "
                "get a Wikipedia article or Wikidata entry, add Organization or Person schema "
                "to your homepage, and ensure your name/address/phone is consistent across the web."
            ),
        })

    if data.get("images"):
        features.append({
            "feature": "Image Pack",
            "icon": "images",
            "present": True,
            "traffic_impact": "medium",
            "recommendation": (
                "Add 3–5 high-quality images with descriptive filenames (keyword-phrase.jpg) "
                "and alt text containing the keyword. Submit an image sitemap. "
                "Images with unique visual value outperform stock photos."
            ),
        })

    if data.get("videos"):
        features.append({
            "feature": "Video Carousel",
            "icon": "video",
            "present": True,
            "traffic_impact": "medium",
            "recommendation": (
                "Create a tutorial or explainer video for this topic. "
                "Upload to YouTube with the keyword in the title and description. "
                "Embed it on your page and add VideoObject schema."
            ),
        })

    if data.get("topStories"):
        features.append({
            "feature": "News Box",
            "icon": "news",
            "present": True,
            "traffic_impact": "medium",
            "recommendation": (
                "This keyword rewards recency. Publish a fresh article or update existing content "
                "with today's date. Add Article schema with datePublished and dateModified. "
                "Register your site in Google News if relevant."
            ),
        })

    if data.get("localResults") or data.get("places"):
        features.append({
            "feature": "Local Pack",
            "icon": "local",
            "present": True,
            "traffic_impact": "high",
            "recommendation": (
                "Optimize your Google Business Profile: add photos, respond to all reviews, "
                "and ensure your name/address/phone matches your website exactly. "
                "Add LocalBusiness schema to your homepage."
            ),
        })

    ads = data.get("ads", [])
    if ads:
        features.append({
            "feature": "Paid Ads",
            "icon": "ads",
            "present": True,
            "traffic_impact": "info",
            "recommendation": (
                f"{len(ads)} paid ad{'s' if len(ads) > 1 else ''} occupy the top of SERP. "
                "High commercial intent- your organic content must offer more depth than ads. "
                "Consider a comparison or 'best of' page to capture users who distrust ads."
            ),
        })

    return features


async def fetch_serp_urls(keyword: str, n: int = SERPER_NUM_RESULTS) -> tuple[list[str], list[dict]]:
    """
    Fetch top N organic Google results for a keyword via Serper.dev API.
    Returns (urls, serp_features)- both extracted from the same single API call.
    Returns ([], []) on API failure.
    """
    payload = {"q": keyword, "num": n, "gl": "us", "hl": "en"}
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(SERPER_ENDPOINT, json=payload, headers=headers)

        if resp.status_code != 200:
            logger.warning(f"Serper API error: status {resp.status_code} for '{keyword}'")
            return [], []

        data         = resp.json()
        organic      = data.get("organic", [])
        urls         = [item["link"] for item in organic if "link" in item]
        serp_features = _extract_serp_features(data)
        logger.info(f"Serper: {len(urls)} competitor URLs, {len(serp_features)} SERP features for '{keyword}'")
        return urls[:n], serp_features

    except Exception as e:
        logger.error(f"Serper fetch error for '{keyword}': {e}")
        return [], []


# ═══════════════════════════════════════════════════════════════════
# SINGLE COMPETITOR SCRAPE
# ═══════════════════════════════════════════════════════════════════

async def _playwright_fetch(url: str) -> str:
    """
    Headless-browser fallback for JS-rendered pages (React, Vue, Angular, etc.).
    Returns rendered HTML string, or "" on failure.
    """
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx  = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
                java_script_enabled=True,
            )
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            html = await page.content()
            await browser.close()
            return html
    except Exception as e:
        logger.debug(f"Playwright fallback failed for {url}: {e}")
        return ""


async def _scrape_one_competitor(
    url: str,
    keyword: str,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """
    Scrape a single competitor URL: fetch HTML, extract features, fetch CC + OPR.
    Tries fast httpx first; falls back to Playwright for JS-rendered pages.
    Returns feature dict or None on failure.
    """
    async with semaphore:
        # ── Fast fetch (httpx) ─────────────────────────────────
        html = ""
        try:
            async with httpx.AsyncClient(
                timeout=SERP_SCRAPE_TIMEOUT,
                follow_redirects=True,
                headers=HEADERS,
            ) as client:
                resp = await client.get(url)

            if resp.status_code == 200:
                html = resp.text
            else:
                logger.debug(f"Competitor HTTP {resp.status_code}: {url}- trying Playwright")

        except Exception as e:
            logger.debug(f"Competitor httpx failed ({e}): {url}- trying Playwright")

        # ── Playwright fallback for JS-rendered / blocked pages ─
        # Trigger if: request failed, or HTML looks like an SPA shell
        # (fewer than 300 words after stripping tags is a strong signal)
        if not html or html.count(" ") < 300:
            logger.info(f"JS-render fallback (Playwright): {url}")
            html = await _playwright_fetch(url)

        if not html:
            logger.debug(f"Competitor skip (no HTML): {url}")
            return None

        # ── Extract features (CPU-bound- run in thread pool) ──
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

    # ── Batch fetch OPR signals for all successful competitors ──
    domains = [c.get("domain", "") for c in competitors if c.get("domain", "")]
    if domains:
        try:
            from services.external_apis import fetch_opr_batch
            opr_results = await fetch_opr_batch(domains)
            for c in competitors:
                dom = c.get("domain", "")
                if dom and dom in opr_results:
                    opr = opr_results[dom]
                    c["opr_page_rank"]    = opr.get("opr_page_rank", 0.0)
                    c["opr_rank_log"]     = opr.get("opr_rank_log", 0.0)
                    c["opr_domain_found"] = opr.get("opr_domain_found", 0)
                else:
                    c["opr_page_rank"]    = 0.0
                    c["opr_rank_log"]     = 0.0
                    c["opr_domain_found"] = 0
        except Exception as e:
            logger.error(f"OPR batch retrieval failed: {e}")
            for c in competitors:
                c["opr_page_rank"]    = 0.0
                c["opr_rank_log"]     = 0.0
                c["opr_domain_found"] = 0
    else:
        for c in competitors:
            c["opr_page_rank"]    = 0.0
            c["opr_rank_log"]     = 0.0
            c["opr_domain_found"] = 0

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
        # No competitor data- default all to 0
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
) -> tuple[list[dict], list[dict]]:
    """
    Full pipeline. Returns (competitors, serp_features).

    1. Check cache- if hit, return cached data for both.
    2. Cache miss: fetch SERP (URLs + features in one call), scrape competitors.
    3. Cache both competitors and SERP features.
    4. Filter out target URL and return.
    """
    from services.serp_cache import (
        get_cached_competitors, set_cached_competitors,
        get_cached_serp_features, set_cached_serp_features,
    )

    cached_comps = get_cached_competitors(keyword)
    cached_feats = get_cached_serp_features(keyword)

    if cached_comps is not None and cached_feats is not None:
        logger.info(f"Full cache hit for keyword: '{keyword}'")
        if target_url:
            cached_comps = [c for c in cached_comps if c.get("url", "").rstrip("/") != target_url.rstrip("/")]
        return cached_comps, cached_feats

    # Cache miss- single Serper.dev call gets both URLs and SERP features
    urls, serp_features = await fetch_serp_urls(keyword)
    competitors         = await scrape_competitors(urls, keyword)

    if competitors:
        set_cached_competitors(keyword, competitors)
    set_cached_serp_features(keyword, serp_features)

    if target_url:
        competitors = [c for c in competitors if c.get("url", "").rstrip("/") != target_url.rstrip("/")]

    return competitors, serp_features
