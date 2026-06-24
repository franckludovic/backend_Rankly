"""
services/brief_generator.py
============================
Content brief generator powered by Gemini 2.0 Flash.
Uses competitor + SERP data already in the audit record- the only new
external call is to the Gemini API (free: 1,500 req/day on AI Studio key).
"""

import asyncio
import json
import logging
import re
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

_MODEL = "gemini-2.0-flash"


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(keyword: str, url: str, on_page: dict,
                  competitors: list[dict], serp_features: list[dict]) -> str:

    # Competitor stats
    wcs = [c.get("word_count", 0) for c in competitors if c.get("word_count")]
    avg_wc = int(sum(wcs) / len(wcs)) if wcs else 1200

    dens = [c.get("keyword_density", 0) for c in competitors if c.get("keyword_density")]
    avg_dens = round(sum(dens) / len(dens), 1) if dens else 1.2

    top_titles = [c.get("page_title") or c.get("url", "") for c in competitors[:5] if c.get("page_title") or c.get("url")]

    # People Also Ask from SERP features
    paa: list[str] = []
    for sf in serp_features:
        if sf.get("feature") in ("paa", "people_also_ask", "People Also Ask"):
            paa = (sf.get("details") or [])[:6]
            break

    current_wc = on_page.get("word_count", 0)
    target_wc  = max(avg_wc + 200, 800)

    lines = [
        "You are a senior SEO content strategist. Create a detailed, actionable content brief.",
        "",
        "AUDIT DATA:",
        f"  Keyword:                  {keyword}",
        f"  Target URL:               {url}",
        f"  Current word count:       {current_wc}",
        f"  Avg competitor WC:        {avg_wc}  (target ≥ {target_wc})",
        f"  Avg keyword density:      {avg_dens}%",
        "",
        "TOP COMPETITOR TITLES:",
    ]
    for t in top_titles:
        lines.append(f"  - {t[:120]}")
    if not top_titles:
        lines.append("  - (no competitor data)")

    lines += ["", "PEOPLE ALSO ASK (live SERP):"]
    for q in paa:
        lines.append(f"  - {q}")
    if not paa:
        lines.append("  - (not available)")

    lines += [
        "",
        "Respond with ONLY a single valid JSON object- no markdown fences, no commentary.",
        "Use this exact schema:",
        json.dumps({
            "title_suggestion":        "SEO-optimised title tag (50-60 chars, keyword near start)",
            "meta_description":        "Compelling meta description (120-160 chars, includes keyword)",
            "summary":                 "2-3 sentence content strategy overview",
            "word_count_target":       target_wc,
            "keyword_density_target":  avg_dens,
            "outline": [
                {"level": "h2", "heading": "...", "notes": "What to cover in this section"},
            ],
            "entities":      ["Entity 1", "Entity 2", "Entity 3", "Entity 4", "Entity 5"],
            "questions":     ["Q1?", "Q2?", "Q3?", "Q4?", "Q5?"],
            "content_gaps":  ["Gap vs competitors 1", "Gap 2", "Gap 3"],
            "internal_link_notes": "Suggestions for linking to/from this page",
        }, indent=2),
        "",
        "IMPORTANT: outline should have 6-10 items mixing h2 and h3. Make headings specific to the keyword.",
    ]

    return "\n".join(lines)


# ── Main async function ───────────────────────────────────────────────────────

async def generate_brief(keyword: str, url: str, on_page: dict,
                         competitors: list[dict], serp_features: list[dict]) -> dict:

    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured- add GEMINI_API_KEY to .env")

    try:
        from google import genai
    except ImportError:
        raise ImportError("google-genai not installed. Run: pip install google-genai")

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = _build_prompt(keyword, url, on_page, competitors, serp_features)

    # SDK is synchronous- run in a thread to keep FastAPI non-blocking
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=_MODEL,
        contents=prompt,
    )
    raw = response.text.strip()

    # Strip any accidental markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$",           "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    try:
        brief = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Gemini returned invalid JSON: {e}\nRaw (first 600 chars): {raw[:600]}")
        raise ValueError(f"Gemini returned malformed JSON: {e}")

    brief.setdefault("keyword", keyword)
    brief.setdefault("url",     url)
    brief["model"] = _MODEL
    return brief
