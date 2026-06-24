"""
services/linker.py
==================
Internal linking suggester- finds pairs of audited pages on the same domain
that have high semantic overlap and recommends linking between them.

Uses the already-loaded all-MiniLM-L6-v2 sentence-transformer (registry.semantic_model)
to encode page text and computes pairwise cosine similarity. No new API calls needed.
"""

import logging
from urllib.parse import urlparse
import numpy as np

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD = 0.42   # cos similarity must exceed this to be surfaced
_MAX_SUGGESTIONS      = 10


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _page_text(on_page: dict, keyword: str) -> str:
    """Build a short text snippet representing the page for embedding."""
    parts = filter(None, [
        on_page.get("title", ""),
        keyword,
        on_page.get("meta_description", ""),
        on_page.get("h1_text", ""),
    ])
    return " ".join(parts)[:512]


def get_linking_suggestions(user_id: str, domain: str, all_audits: list[dict]) -> dict:
    """
    Compute pairwise cosine similarity between all audited pages on `domain`
    and return linking opportunities sorted by similarity.

    Parameters
    ----------
    user_id    : current user (already filtered at call site)
    domain     : e.g. "example.com"- normalised, no www
    all_audits : list of raw audit rows from Supabase (already pre-loaded by caller)
    """
    from models.model_registry import registry

    sem_model = registry.semantic_model
    if sem_model is None:
        return {"suggestions": [], "page_count": 0, "error": "semantic model unavailable"}

    # Filter to this domain only
    pages = []
    for a in all_audits:
        if _domain(a.get("url", "")) != domain:
            continue
        resp    = a.get("response", {})
        on_page = resp.get("on_page", {})
        kw      = a.get("keyword", "")
        text    = _page_text(on_page, kw)
        if not text.strip():
            continue
        pages.append({
            "id":             a["id"],
            "url":            a["url"],
            "keyword":        kw,
            "title":          on_page.get("title", a["url"]),
            "internal_links": on_page.get("internal_links", 0),
            "word_count":     on_page.get("word_count", 0),
            "text":           text,
        })

    if len(pages) < 2:
        return {"suggestions": [], "page_count": len(pages)}

    # Encode all pages in one batch
    try:
        texts  = [p["text"] for p in pages]
        vecs   = sem_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return {"suggestions": [], "page_count": len(pages), "error": str(e)}

    # Pairwise cosine similarity (dot product of normalised vectors)
    sim_matrix = np.dot(vecs, vecs.T).astype(float)

    suggestions = []
    n = len(pages)
    for i in range(n):
        for j in range(i + 1, n):
            sim = float(sim_matrix[i, j])
            if sim < _SIMILARITY_THRESHOLD:
                continue

            # Source = page with more internal links (it has link juice to pass)
            # Target = page with fewer internal links (needs the boost)
            src, tgt = (pages[i], pages[j]) if pages[i]["internal_links"] >= pages[j]["internal_links"] else (pages[j], pages[i])

            suggestions.append({
                "source_url":     src["url"],
                "source_title":   src["title"] or src["url"],
                "source_keyword": src["keyword"],
                "target_url":     tgt["url"],
                "target_title":   tgt["title"] or tgt["url"],
                "target_keyword": tgt["keyword"],
                "similarity":     round(sim * 100),
                "target_links":   tgt["internal_links"],
                "recommendation": _recommendation(src, tgt, sim),
            })

    suggestions.sort(key=lambda s: -s["similarity"])
    return {
        "suggestions": suggestions[:_MAX_SUGGESTIONS],
        "page_count":  len(pages),
        "domain":      domain,
    }


def _recommendation(src: dict, tgt: dict, sim: float) -> str:
    kw  = tgt["keyword"] or "this topic"
    pct = round(sim * 100)
    if pct >= 75:
        return f'High semantic overlap ({pct}%)- add a contextual link from "{src["title"][:50]}" pointing to the {kw!r} page.'
    if pct >= 60:
        return f'Moderate overlap ({pct}%)- consider a "Related:" link or sidebar mention from {src["url"].split("/")[-1] or src["url"]} to the {kw!r} page.'
    return f'Potential relevance ({pct}%)- the {kw!r} page has only {tgt["target_links"] if hasattr(tgt, "target_links") else tgt["internal_links"]} internal links; a link from the {src["keyword"]!r} page could help.'
