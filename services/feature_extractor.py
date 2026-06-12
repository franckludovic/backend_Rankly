"""
services/feature_extractor.py
==============================
Scrapes a URL and extracts all on-page SEO features.
Reuses the same logic as your html_scraper.py pipeline.

Also accepts pre-extracted features from the browser extension
(so the extension doesn't need to send raw HTML — just features).
"""

import re
import requests
import numpy as np
import logging
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from config import SCRAPER_CONNECT_TIMEOUT, SCRAPER_READ_TIMEOUT, SCRAPER_MIN_WORD_COUNT

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── Intent classifier (same as in your notebook) ─────────────────

def classify_intent(keyword: str) -> int:
    kw = keyword.lower().strip()
    if "near me" in kw or " in " in kw:
        return 3
    transactional = ["buy","price","cheap","best","top","review","vs",
                     "hire","cost","affordable","service","agency","tool","software"]
    if any(w in kw.split() or f" {w} " in f" {kw} " for w in transactional):
        return 1
    informational = ["how","what","why","when","who","where","guide","tips",
                     "ways","steps","tutorial","learn","examples","difference",
                     "meaning","definition","benefits","types","list"]
    if any(kw.startswith(w) or f" {w} " in f" {kw} " for w in informational):
        return 2
    return 0


def extract_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain.lower()
    except Exception:
        return ""


# ── Keyword helpers ───────────────────────────────────────────────

def keyword_in_text(keyword: str, text: str) -> int:
    if not text or not keyword:
        return 0
    return 1 if keyword.lower() in text.lower() else 0

def keyword_position_in_title(keyword: str, title: str) -> int | str:
    if not title or not keyword:
        return "absent"
    pos = title.lower().find(keyword.lower())
    if pos == -1:
        return "absent"
    return pos


# ── Main feature extraction ───────────────────────────────────────

def extract_features_from_url(url: str, keyword: str) -> dict:
    """
    Scrape a URL and extract all SEO features.
    Returns feature dict or raises ValueError if page inaccessible.
    """
    # Fetch HTML
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=(SCRAPER_CONNECT_TIMEOUT, SCRAPER_READ_TIMEOUT),
            allow_redirects=True
        )
        resp.raise_for_status()
        html = resp.text
    except requests.exceptions.Timeout:
        raise ValueError(f"Page timed out after {SCRAPER_READ_TIMEOUT}s: {url}")
    except requests.exceptions.ConnectionError:
        raise ValueError(f"Could not connect to: {url}")
    except requests.exceptions.HTTPError as e:
        raise ValueError(f"HTTP error {e.response.status_code}: {url}")

    return extract_features_from_html(html, url, keyword)


def extract_features_from_html(html: str, url: str, keyword: str) -> dict:
    """
    Extract features from raw HTML string.
    Used by both URL scraping and extension feature submission.
    """
    kw     = keyword.strip()
    domain = extract_domain(url)
    is_https = 1 if url.startswith("https://") else 0

    # ── Capture raw HTML metrics before stripping ──────────────
    raw_html_bytes   = len(html.encode("utf-8"))
    raw_html_size_kb = round(raw_html_bytes / 1024, 2)
    soup_full        = BeautifulSoup(html, "lxml")
    total_dom_elements = len(soup_full.find_all())
    js_files_count     = len([s for s in soup_full.find_all("script") if s.get("src")])
    css_files_count    = len(soup_full.find_all("link", rel="stylesheet"))
    has_og_tags        = 1 if soup_full.find(
        "meta", attrs={"property": re.compile(r"^og:", re.I)}
    ) else 0
    has_robots_meta    = 1 if soup_full.find(
        "meta", attrs={"name": re.compile(r"^robots$", re.I)}
    ) else 0

    # ── Parse with BeautifulSoup ───────────────────────────────
    soup = soup_full

    # ── Title ──────────────────────────────────────────────────
    title_tag    = soup.find("title")
    title_text   = title_tag.get_text(strip=True) if title_tag else ""
    title_length = len(title_text)
    title_has_kw = keyword_in_text(kw, title_text)
    kw_pos_title = keyword_position_in_title(kw, title_text)

    # ── Meta description ───────────────────────────────────────
    meta_desc    = soup.find("meta", {"name": re.compile(r"^description$", re.I)})
    meta_content = meta_desc.get("content", "") if meta_desc else ""
    meta_desc_present   = 1 if meta_content else 0
    meta_desc_length    = len(meta_content)
    meta_desc_has_kw    = keyword_in_text(kw, meta_content)

    # ── Headings ───────────────────────────────────────────────
    h1_tags   = soup.find_all("h1")
    h2_tags   = soup.find_all("h2")
    h3_tags   = soup.find_all("h3")
    h1_count  = len(h1_tags)
    h2_count  = len(h2_tags)
    h3_count  = len(h3_tags)
    total_heading_count = h1_count + h2_count + h3_count
    h1_has_kw = 1 if any(keyword_in_text(kw, h.get_text()) for h in h1_tags) else 0

    # ── Strip noise ────────────────────────────────────────────
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    # ── Content ────────────────────────────────────────────────
    clean_text  = soup.get_text(separator=" ")
    words       = [w for w in clean_text.split() if w.strip()]
    word_count  = len(words)

    if word_count < SCRAPER_MIN_WORD_COUNT:
        raise ValueError(
            f"Page has too little content ({word_count} words). "
            "May be a redirect, error page, or JavaScript-rendered app."
        )

    kw_freq    = clean_text.lower().count(kw.lower())
    kw_density = round((kw_freq / word_count) * 100, 4) if word_count > 0 else 0

    # ── Images ────────────────────────────────────────────────
    images          = soup.find_all("img")
    image_count     = len(images)
    has_images      = 1 if image_count > 0 else 0
    alt_count       = sum(1 for img in images if img.get("alt", "").strip())
    alt_has_kw      = sum(1 for img in images if keyword_in_text(kw, img.get("alt", "")))

    # ── Paragraphs ────────────────────────────────────────────
    paragraph_count = len(soup.find_all("p"))

    # ── Links ─────────────────────────────────────────────────
    all_links    = soup.find_all("a", href=True)
    internal     = [a for a in all_links if domain in a["href"] or
                    a["href"].startswith("/")]
    external     = [a for a in all_links if a["href"].startswith("http") and
                    domain not in a["href"]]
    internal_link_count = len(internal)
    external_link_count = len(external)

    # ── Technical ─────────────────────────────────────────────
    has_viewport = 1 if soup.find("meta", {"name": re.compile(r"^viewport$", re.I)}) else 0
    has_schema   = 1 if soup.find("script", {"type": "application/ld+json"}) else 0
    has_canonical= 1 if soup.find("link", {"rel": "canonical"}) else 0

    # ── Text-to-HTML ratio ────────────────────────────────────
    text_bytes        = len(clean_text.encode("utf-8"))
    text_to_html_ratio= round(text_bytes / raw_html_bytes, 4) if raw_html_bytes > 0 else 0

    # ── Engineered features ───────────────────────────────────
    optimal_title_length = 1 if 50 <= title_length <= 60 else 0
    optimal_meta_length  = 1 if 120 <= meta_desc_length <= 160 else 0
    alt_coverage         = round(alt_count / image_count, 4) if image_count > 0 else 0
    heading_density      = round(total_heading_count / word_count, 6) if word_count > 0 else 0
    keyword_signal_count = (title_has_kw + meta_desc_has_kw +
                            h1_has_kw + (1 if alt_has_kw > 0 else 0))
    technical_score      = (has_viewport + is_https + has_schema + has_canonical)

    # ── Keyword features ──────────────────────────────────────
    kw_words           = kw.split()
    keyword_word_count = len(kw_words)
    is_long_tail       = 1 if keyword_word_count >= 4 else 0
    query_intent       = classify_intent(kw)

    return {
        # Metadata
        "url"                      : url,
        "domain"                   : domain,
        "keyword"                  : kw,

        # Title (3)
        "title_length"             : title_length,
        "title_has_keyword"        : title_has_kw,
        "keyword_position_in_title": kw_pos_title,

        # Meta description (3)
        "meta_desc_length"         : meta_desc_length,
        "meta_desc_has_keyword"    : meta_desc_has_kw,
        "meta_desc_present"        : meta_desc_present,

        # Headings (5)
        "h1_count"                 : h1_count,
        "h1_has_keyword"           : h1_has_kw,
        "h2_count"                 : h2_count,
        "h3_count"                 : h3_count,
        "total_heading_count"      : total_heading_count,

        # Content (8)
        "word_count"               : word_count,
        "keyword_frequency"        : kw_freq,
        "keyword_density"          : kw_density,
        "has_images"               : has_images,
        "image_count"              : image_count,
        "images_with_alt_count"    : alt_count,
        "alt_has_keyword"          : alt_has_kw,
        "paragraph_count"          : paragraph_count,

        # Links (2)
        "internal_link_count"      : internal_link_count,
        "external_link_count"      : external_link_count,

        # Technical (4)
        "has_viewport_meta"        : has_viewport,
        "is_https"                 : is_https,
        "has_schema_markup"        : has_schema,
        "has_canonical_tag"        : has_canonical,

        # Engineered (6)
        "domain_frequency"         : 0,   # not available at inference
        "technical_score"          : technical_score,
        "keyword_signal_count"     : keyword_signal_count,
        "optimal_title_length"     : optimal_title_length,
        "optimal_meta_length"      : optimal_meta_length,
        "alt_coverage"             : alt_coverage,
        "heading_density"          : heading_density,

        # New HTML (7)
        "raw_html_size_kb"         : raw_html_size_kb,
        "total_dom_elements"       : total_dom_elements,
        "js_files_count"           : js_files_count,
        "css_files_count"          : css_files_count,
        "has_og_tags"              : has_og_tags,
        "has_robots_meta"          : has_robots_meta,
        "text_to_html_ratio"       : text_to_html_ratio,

        # Keyword features (3)
        "keyword_word_count"       : keyword_word_count,
        "is_long_tail"             : is_long_tail,
        "query_intent"             : query_intent,

        # Placeholders — filled by predictor.py
        "lighthouse_seo_score"     : -1,
        "opr_page_rank"            : 0,
        "opr_rank_log"             : 0,
        "opr_domain_found"         : 0,
        "keyword_competition"      : 0,
    }
