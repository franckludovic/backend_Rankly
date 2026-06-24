"""
services/bulk_auditor.py
========================
Bulk audit runner. Accepts up to 50 URLs (from a sitemap or direct input),
runs run_full_analysis() for each with asyncio.Semaphore(5) for concurrency,
and stores each completed audit in the database.

Job state is held in-memory (per-process). Fine for single-server deployments.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
import httpx

logger = logging.getLogger(__name__)

_SITEMAP_NS    = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_MAX_SITEMAP   = 50
_MAX_CONCURRENT = 5


# ── Sitemap parser ────────────────────────────────────────────────────────────

async def parse_sitemap(url: str, _depth: int = 0) -> list[str]:
    """Fetch and parse a sitemap (or sitemap index). Returns up to 50 URLs."""
    if _depth > 2:
        return []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Rankly/1.0 SitemapScanner"})
        resp.raise_for_status()
        xml_text = resp.text

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML in sitemap: {e}")

    # Sitemap index → recurse into first child sitemap
    index_locs = root.findall("sm:sitemap/sm:loc", _SITEMAP_NS)
    if index_locs:
        child_url = (index_locs[0].text or "").strip()
        if child_url:
            return await parse_sitemap(child_url, _depth + 1)

    # Try without namespace
    locs = root.findall(".//loc")
    if not locs:
        locs = root.findall("sm:url/sm:loc", _SITEMAP_NS)

    urls = [(loc.text or "").strip() for loc in locs if (loc.text or "").strip()]
    return urls[:_MAX_SITEMAP]


# ── Job ───────────────────────────────────────────────────────────────────────

class BulkJob:
    def __init__(self, job_id: str, user_id: str, keyword: str):
        self.job_id   = job_id
        self.user_id  = user_id
        self.keyword  = keyword
        self.total    = 0
        self.completed= 0
        self.failed   = 0
        self.done     = False
        self.results: list[dict] = []
        self.errors:  list[dict] = []

    def snapshot(self) -> dict:
        return {
            "job_id":    self.job_id,
            "keyword":   self.keyword,
            "total":     self.total,
            "completed": self.completed,
            "failed":    self.failed,
            "done":      self.done,
            "results":   list(self.results),
            "errors":    list(self.errors),
        }

    async def run(self, urls: list[str]) -> None:
        from services.audit_engine import run_full_analysis
        from storage import audit_repository

        self.total = len(urls)
        sem        = asyncio.Semaphore(_MAX_CONCURRENT)

        async def _one(url: str) -> None:
            async with sem:
                try:
                    data    = await run_full_analysis(url, self.keyword)
                    pred    = data.get("prediction", {})
                    on_page = data.get("on_page", {})
                    qual    = (pred.get("classification") or {}).get("quality", "LOW")
                    base    = {"HIGH": 85, "MEDIUM": 60}.get(qual, 35)
                    tech    = (on_page.get("technical_score") or 0) * 5
                    score   = min(100, base + tech)
                    rank    = round((pred.get("regression") or {}).get("predicted_rank", 50))

                    try:
                        rec = audit_repository.create_audit(self.user_id, url, self.keyword, data)
                        audit_id = (rec or {}).get("id")
                    except Exception:
                        audit_id = None

                    self.results.append({
                        "url":            url,
                        "audit_id":       audit_id,
                        "seo_score":      round(score),
                        "quality":        qual,
                        "predicted_rank": rank,
                        "issues":         len(data.get("recommendations", [])),
                        "word_count":     on_page.get("word_count", 0),
                        "has_schema":     bool(on_page.get("has_schema_markup")),
                        "title_has_kw":   bool(on_page.get("title_has_kw")),
                    })
                    self.completed += 1
                    logger.info(f"Bulk [{self.completed}/{self.total}] ✓ {url}")

                except Exception as e:
                    self.errors.append({"url": url, "error": str(e)[:120]})
                    self.failed    += 1
                    self.completed += 1
                    logger.warning(f"Bulk [{self.completed}/{self.total}] ✗ {url}: {e}")

        await asyncio.gather(*(_one(url) for url in urls))
        self.done = True
        logger.info(f"Bulk job {self.job_id} done: {self.completed - self.failed} ok, {self.failed} failed")
