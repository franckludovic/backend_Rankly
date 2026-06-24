"""
services/feature_extractor.py
==============================
Scrapes a URL and extracts all on-page SEO features aligned with the V6 models.

Key changes vs. previous version:
  - semantic_relevance is now real cosine similarity from all-MiniLM-L6-v2
    (falls back to tfidf_relevance proxy if the semantic model is unavailable)
  - All V6 regressor features are present (CC features / query-z features default 0)
  - domain_frequency_log, relevance_to_authority_ratio,
    semantic_to_authority_ratio derived correctly

Also accepts pre-extracted features from the browser extension
(so the extension doesn't need to send raw HTML- just features).
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

# Maximum characters of body text sent to the semantic model
# (MiniLM truncates at 256 word-pieces; ~1 000 chars is safe)
_SEMANTIC_BODY_CHARS = 1000


# ── Intent classifier (same as in your notebook) ──────────────────

def classify_intent(keyword: str) -> int:
    kw = keyword.lower().strip()
    if "near me" in kw or " in " in kw:
        return 3
    transactional = ["buy", "price", "cheap", "best", "top", "review", "vs",
                     "hire", "cost", "affordable", "service", "agency", "tool", "software"]
    if any(w in kw.split() or f" {w} " in f" {kw} " for w in transactional):
        return 1
    informational = ["how", "what", "why", "when", "who", "where", "guide", "tips",
                     "ways", "steps", "tutorial", "learn", "examples", "difference",
                     "meaning", "definition", "benefits", "types", "list"]
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


# ── Semantic similarity ───────────────────────────────────────────

def compute_semantic_relevance(keyword: str, body_text: str, tfidf_fallback: float) -> float:
    """
    Compute cosine similarity between the keyword and page body text
    using the loaded all-MiniLM-L6-v2 sentence-transformer.

    Returns a float in [0, 1].  Falls back to tfidf_fallback when the
    semantic model is not available.
    """
    try:
        # Import here to avoid circular imports at module load time
        from models.model_registry import registry
        sem_model = registry.semantic_model

        if sem_model is None:
            return float(tfidf_fallback)

        # Truncate body to keep encoding fast
        body_snippet = body_text[:_SEMANTIC_BODY_CHARS].strip()
        if not body_snippet:
            return 0.0

        # Encode both texts; normalize=True gives unit vectors → dot = cosine sim
        vecs = sem_model.encode(
            [keyword, body_snippet],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        cosine_sim = float(np.dot(vecs[0], vecs[1]))
        # Clip to [0, 1]- cosine can be slightly negative for unrelated texts
        return round(max(0.0, cosine_sim), 6)

    except Exception as e:
        logger.warning(f"Semantic similarity failed, using TF-IDF proxy: {e}")
        return float(tfidf_fallback)


# ── Page type detection (for schema generation) ──────────────────

def detect_page_type(soup, title: str, keyword: str) -> tuple[str, dict]:
    """
    Detect the most likely page type for schema markup selection.
    Must be called before noise-stripping decomposition.
    Returns (page_type_str, context_dict).
    """
    title_lower = title.lower()
    full_text   = soup.get_text(separator=" ", strip=True)
    text_lower  = full_text.lower()

    # Product
    has_price       = bool(re.search(r"\$\s*\d+|price\s*:|\badd to cart\b|\bbuy now\b", text_lower))
    has_offer_prop  = bool(soup.find(attrs={"itemprop": re.compile(r"price|offer", re.I)}))
    if has_price or has_offer_prop:
        return "Product", {}

    # FAQPage
    details_tags = soup.find_all("details")
    q_marks      = text_lower.count("?")
    faq_signal   = bool(re.search(r"faq|frequently asked|questions?\s+and\s+answers?", text_lower))
    if (details_tags and q_marks >= 2) or (faq_signal and q_marks >= 3):
        questions = [d.find("summary").get_text(strip=True) for d in details_tags if d.find("summary")][:6]
        if not questions:
            questions = [t.get_text(strip=True) for t in soup.find_all(["h3", "h4"]) if t.get_text(strip=True).endswith("?")][:6]
        return "FAQPage", {"questions": questions}

    # HowTo
    how_to_title = bool(re.search(r"^how (to|do|can|should)\b", title_lower))
    ol_tags = soup.find_all("ol")
    if how_to_title and ol_tags:
        steps = [li.get_text(strip=True)[:150] for li in ol_tags[0].find_all("li")][:8]
        return "HowTo", {"steps": steps}

    # Article
    has_article_elem = bool(soup.find("article"))
    has_time_elem    = bool(soup.find("time"))
    has_author       = (
        bool(soup.find(class_=re.compile(r"author|byline", re.I))) or
        bool(soup.find(attrs={"itemprop": "author"}))
    )
    date_pat = bool(re.search(
        r"\b(january|february|march|april|may|june|july|august|september|"
        r"october|november|december)\b\s+\d{1,2},?\s+\d{4}", text_lower
    ))
    if has_article_elem or (has_time_elem and (has_author or date_pat)):
        return "Article", {}

    # LocalBusiness
    phone_pat   = bool(re.search(r"\(\d{3}\)\s*\d{3}[-.\s]\d{4}|\+\d[\d\s\-]{7,15}", full_text))
    address_pat = bool(re.search(
        r"\d{1,5}\s+\w[\w\s]+\b(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln)\b",
        full_text, re.I
    ))
    has_maps    = bool(soup.find("iframe", src=re.compile(r"google.*map|maps\.google", re.I)))
    if (phone_pat and address_pat) or has_maps:
        return "LocalBusiness", {}

    # Article fallback for long content with a date element
    if len(full_text.split()) > 500 and has_time_elem:
        return "Article", {}

    return "WebPage", {}


# ── Main feature extraction ───────────────────────────────────────

def extract_features_from_url(url: str, keyword: str) -> dict:
    """
    Scrape a URL and extract all SEO features.
    Returns feature dict or raises ValueError if page inaccessible.
    """
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
    Produces the full V6 feature set required by both XGBoost models.
    """
    kw       = keyword.strip()
    domain   = extract_domain(url)
    is_https = 1 if url.startswith("https://") else 0

    # ── Raw HTML metrics (before stripping) ───────────────────────
    raw_html_bytes    = len(html.encode("utf-8"))
    raw_html_size_kb  = round(raw_html_bytes / 1024, 2)
    soup_full         = BeautifulSoup(html, "lxml")
    total_dom_elements = len(soup_full.find_all())
    js_files_count     = len([s for s in soup_full.find_all("script") if s.get("src")])
    css_files_count    = len(soup_full.find_all("link", rel="stylesheet"))
    has_og_tags        = 1 if soup_full.find(
        "meta", attrs={"property": re.compile(r"^og:", re.I)}
    ) else 0
    has_robots_meta    = 1 if soup_full.find(
        "meta", attrs={"name": re.compile(r"^robots$", re.I)}
    ) else 0

    # ── Max DOM Depth ──
    max_depth = 0
    root = soup_full.find("html") or soup_full
    stack = [(root, 1)]
    while stack:
        elem, depth = stack.pop()
        if depth > max_depth:
            max_depth = depth
        if hasattr(elem, "children"):
            for child in elem.children:
                if child.name is not None:
                    stack.append((child, depth + 1))
    dom_depth = max_depth

    # ── Layout Shift Images Count ──
    images_full = soup_full.find_all("img")
    missing_img_dimensions = 0
    for img in images_full:
        width = img.get("width")
        height = img.get("height")
        if not (width and width.strip() and height and height.strip()):
            missing_img_dimensions += 1

    # ── Modern Image Formats Ratio ──
    modern_img_count = 0
    total_imgs = len(images_full)
    for img in images_full:
        src = img.get("src", "").lower().strip()
        clean_src = src.split("?")[0].split("#")[0]
        if clean_src.endswith((".webp", ".svg", ".avif")):
            modern_img_count += 1
    modern_img_ratio = round(modern_img_count / total_imgs, 4) if total_imgs > 0 else 0.0

    soup = soup_full

    # ── Title ──────────────────────────────────────────────────────
    title_tag    = soup.find("title")
    title_text   = title_tag.get_text(strip=True) if title_tag else ""
    title_length = len(title_text)
    title_has_kw = keyword_in_text(kw, title_text)
    kw_pos_title = keyword_position_in_title(kw, title_text)

    # ── Meta description ───────────────────────────────────────────
    meta_desc    = soup.find("meta", {"name": re.compile(r"^description$", re.I)})
    meta_content = meta_desc.get("content", "") if meta_desc else ""
    meta_desc_present = 1 if meta_content else 0
    meta_desc_length  = len(meta_content)
    meta_desc_has_kw  = keyword_in_text(kw, meta_content)

    # ── Headings ───────────────────────────────────────────────────
    h1_tags   = soup.find_all("h1")
    h2_tags   = soup.find_all("h2")
    h3_tags   = soup.find_all("h3")
    h1_count  = len(h1_tags)
    h2_count  = len(h2_tags)
    h3_count  = len(h3_tags)
    total_heading_count = h1_count + h2_count + h3_count
    h1_has_kw = 1 if any(keyword_in_text(kw, h.get_text()) for h in h1_tags) else 0
    h1_text   = h1_tags[0].get_text(strip=True) if h1_tags else ""

    # ── Detect page type before stripping nav/footer/header ──────────
    page_type, schema_context = detect_page_type(soup, title_text, kw)

    # ── Strip noise ────────────────────────────────────────────────
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # ── Content ────────────────────────────────────────────────────
    clean_text = soup.get_text(separator=" ")
    words      = [w for w in clean_text.split() if w.strip()]
    word_count = len(words)

    if word_count < SCRAPER_MIN_WORD_COUNT:
        raise ValueError(
            f"Page has too little content ({word_count} words). "
            "May be a redirect, error page, or JavaScript-rendered app."
        )

    kw_freq    = clean_text.lower().count(kw.lower())
    kw_density = round((kw_freq / word_count) * 100, 4) if word_count > 0 else 0

    # ── Images ────────────────────────────────────────────────────
    images      = soup.find_all("img")
    image_count = len(images)
    has_images  = 1 if image_count > 0 else 0
    alt_count   = sum(1 for img in images if img.get("alt", "").strip())
    alt_has_kw  = sum(1 for img in images if keyword_in_text(kw, img.get("alt", "")))

    # ── Paragraphs ────────────────────────────────────────────────
    paragraph_count = len(soup.find_all("p"))

    # ── Links ─────────────────────────────────────────────────────
    all_links = soup.find_all("a", href=True)
    internal  = [a for a in all_links if domain in a["href"] or
                 a["href"].startswith("/")]
    external  = [a for a in all_links if a["href"].startswith("http") and
                 domain not in a["href"]]
    internal_link_count = len(internal)
    external_link_count = len(external)

    # ── Technical ─────────────────────────────────────────────────
    has_viewport  = 1 if soup.find("meta", {"name": re.compile(r"^viewport$", re.I)}) else 0
    has_schema    = 1 if soup.find("script", {"type": "application/ld+json"}) else 0
    canonical_tag = soup.find("link", {"rel": "canonical"})
    has_canonical = 1 if canonical_tag else 0
    canonical_url = canonical_tag.get("href", "").strip() if canonical_tag else ""
    robots_meta   = soup.find("meta", {"name": re.compile(r"^robots$", re.I)})
    index_status  = robots_meta.get("content", "").strip() if robots_meta else "index, follow"

    # ── Text-to-HTML ratio ────────────────────────────────────────
    text_bytes        = len(clean_text.encode("utf-8"))
    text_to_html_ratio = round(text_bytes / raw_html_bytes, 4) if raw_html_bytes > 0 else 0

    # ── Engineered features ───────────────────────────────────────
    optimal_title_length = 1 if 50 <= title_length <= 60 else 0
    optimal_meta_length  = 1 if 120 <= meta_desc_length <= 160 else 0
    alt_coverage         = round(alt_count / image_count, 4) if image_count > 0 else 0
    heading_density      = round(total_heading_count / word_count, 6) if word_count > 0 else 0
    keyword_signal_count = (title_has_kw + meta_desc_has_kw +
                            h1_has_kw + (1 if alt_has_kw > 0 else 0))
    technical_score      = (has_viewport + is_https + has_schema + has_canonical)

    # ── Advanced keyword features ─────────────────────────────────
    kw_words           = kw.split()
    keyword_word_count = len(kw_words)
    is_long_tail       = 1 if keyword_word_count >= 4 else 0
    query_intent       = classify_intent(kw)

    keyword_exact_match       = 1 if kw_freq > 0 else 0
    keyword_exact_match_count = kw_freq

    text_lower = clean_text.lower()
    first_100_words = " ".join(text_lower.split()[:100])
    keyword_in_first_100_words = 1 if kw.lower() in first_100_words else 0

    keyword_proximity_score = 0.0
    if len(kw_words) > 1:
        first_idx = min(
            (text_lower.find(w) for w in kw_words if w in text_lower), default=-1
        )
        last_idx = max(
            (text_lower.rfind(w) for w in kw_words if w in text_lower), default=-1
        )
        if first_idx >= 0 and last_idx > first_idx:
            span = (last_idx - first_idx) / max(len(text_lower), 1)
            keyword_proximity_score = round(1 - span, 4)

    keyword_variations_count = sum(1 for w in kw_words if w.lower() in text_lower)

    tfidf_relevance = round(kw_freq / np.log1p(word_count), 4) if word_count > 0 else 0

    keyword_prominence_score = round(keyword_signal_count / 3, 4) if keyword_signal_count > 0 else 0

    # Locate primary content container if it exists
    main_tag = soup.find("main") or soup.find("article")
    if main_tag:
        semantic_body_text = main_tag.get_text(separator=" ")
    else:
        semantic_body_text = clean_text

    # ── Real semantic relevance (all-MiniLM-L6-v2) ───────────────
    # Uses cosine similarity between keyword embedding and body text embedding.
    # Falls back to tfidf_relevance if the semantic model is unavailable.
    semantic_relevance = compute_semantic_relevance(kw, semantic_body_text, tfidf_relevance)

    # ── Off-page / authority features ────────────────────────────────
    # These are populated AFTER scraping by the route handler:
    #   - opr_* : from fetch_all_external_signals() / fetch_opr_score()
    #   - cc_*  : from cc_graph.fetch_cc_signals() (SQLite lookup or CDX fallback)
    # All default to 0 here; the route handler overwrites them.
    opr_page_rank    = 0.0
    opr_rank_log     = 0.0
    opr_domain_found = 0

    # domain_frequency is not computable at single-URL inference time
    domain_frequency     = 0
    domain_frequency_log = 0.0   # log1p(domain_frequency)- stays 0 at inference

    # Common Crawl graph signals- filled by cc_graph.fetch_cc_signals()
    # after extract_features_from_html() returns.  Defaults here are overwritten.
    cc_pagerank              = 0.0
    cc_harmonic_centrality   = 0.0
    cc_referring_domains_log = 0.0
    cc_found                 = 0

    # Authority ratios- recomputed in predictor._enrich_with_external()
    # once cc_pagerank is known. Placeholder 0 here.
    relevance_to_authority_ratio = 0.0
    semantic_to_authority_ratio  = 0.0

    # Keyword competition / SERP signals (not available from HTML alone)
    keyword_competition  = 0.0
    keyword_avg_position = 0.0
    keyword_position_std = 0.0

    # Query-relative z-score and percentile features
    # (require a competitor set- default to 0 = average/median)
    _vs_query_z_features = {
        "cc_referring_domains_log_vs_query_z"    : 0.0,
        "cc_pagerank_vs_query_z"                 : 0.0,
        "cc_harmonic_centrality_vs_query_z"      : 0.0,
        "opr_page_rank_vs_query_z"               : 0.0,
        "opr_rank_log_vs_query_z"                : 0.0,
        "domain_frequency_log_vs_query_z"        : 0.0,
        "lighthouse_seo_score_vs_query_z"        : 0.0,
        "technical_score_vs_query_z"             : 0.0,
        "tfidf_relevance_vs_query_z"             : 0.0,
        "word_count_vs_query_z"                  : 0.0,
        "internal_link_count_vs_query_z"         : 0.0,
        "keyword_density_vs_query_z"             : 0.0,
        "keyword_frequency_vs_query_z"           : 0.0,
        "keyword_proximity_score_vs_query_z"     : 0.0,
        "keyword_prominence_score_vs_query_z"    : 0.0,
        "semantic_relevance_vs_query_z"          : 0.0,
        "keyword_variations_count_vs_query_z"    : 0.0,
    }
    _vs_query_pct_features = {
        "cc_referring_domains_log_vs_query_pct"  : 0.0,
        "cc_pagerank_vs_query_pct"               : 0.0,
        "cc_harmonic_centrality_vs_query_pct"    : 0.0,
        "opr_page_rank_vs_query_pct"             : 0.0,
        "opr_rank_log_vs_query_pct"              : 0.0,
        "domain_frequency_log_vs_query_pct"      : 0.0,
        "lighthouse_seo_score_vs_query_pct"      : 0.0,
        "technical_score_vs_query_pct"           : 0.0,
        "tfidf_relevance_vs_query_pct"           : 0.0,
        "word_count_vs_query_pct"                : 0.0,
        "internal_link_count_vs_query_pct"       : 0.0,
        "keyword_density_vs_query_pct"           : 0.0,
        "keyword_frequency_vs_query_pct"         : 0.0,
        "keyword_proximity_score_vs_query_pct"   : 0.0,
        "keyword_prominence_score_vs_query_pct"  : 0.0,
        "semantic_relevance_vs_query_pct"        : 0.0,
        "keyword_variations_count_vs_query_pct"  : 0.0,
    }

    features = {
        # ── Metadata (not model inputs) ──────────────────────────
        "url"                      : url,
        "domain"                   : domain,
        "keyword"                  : kw,

        # ── Title (3) ────────────────────────────────────────────
        "title_length"             : title_length,
        "title_has_keyword"        : title_has_kw,
        "keyword_position_in_title": kw_pos_title,

        # ── Meta description (3) ─────────────────────────────────
        "meta_desc_length"         : meta_desc_length,
        "meta_desc_has_keyword"    : meta_desc_has_kw,
        "meta_desc_present"        : meta_desc_present,

        # ── Headings (5) ─────────────────────────────────────────
        "h1_count"                 : h1_count,
        "h1_has_keyword"           : h1_has_kw,
        "h2_count"                 : h2_count,
        "h3_count"                 : h3_count,
        "total_heading_count"      : total_heading_count,

        # ── Content (8) ──────────────────────────────────────────
        "word_count"               : word_count,
        "keyword_frequency"        : kw_freq,
        "keyword_density"          : kw_density,
        "has_images"               : has_images,
        "image_count"              : image_count,
        "images_with_alt_count"    : alt_count,
        "alt_has_keyword"          : alt_has_kw,
        "paragraph_count"          : paragraph_count,

        # ── Links (2) ────────────────────────────────────────────
        "internal_link_count"      : internal_link_count,
        "external_link_count"      : external_link_count,

        # ── Technical (4) ────────────────────────────────────────
        "has_viewport_meta"        : has_viewport,
        "is_https"                 : is_https,
        "has_schema_markup"        : has_schema,
        "has_canonical_tag"        : has_canonical,

        # ── Engineered (7) ───────────────────────────────────────
        "technical_score"          : technical_score,
        "keyword_signal_count"     : keyword_signal_count,
        "optimal_title_length"     : optimal_title_length,
        "optimal_meta_length"      : optimal_meta_length,
        "alt_coverage"             : alt_coverage,
        "heading_density"          : heading_density,

        # ── New HTML (7) ─────────────────────────────────────────
        "raw_html_size_kb"         : raw_html_size_kb,
        "total_dom_elements"       : total_dom_elements,
        "js_files_count"           : js_files_count,
        "css_files_count"          : css_files_count,
        "has_og_tags"              : has_og_tags,
        "has_robots_meta"          : has_robots_meta,
        "text_to_html_ratio"       : text_to_html_ratio,
        "dom_depth"                : dom_depth,
        "missing_img_dimensions"   : missing_img_dimensions,
        "modern_img_ratio"         : modern_img_ratio,

        # ── Keyword features (3) ─────────────────────────────────
        "keyword_word_count"       : keyword_word_count,
        "is_long_tail"             : is_long_tail,
        "query_intent"             : query_intent,

        # ── Advanced keyword features (8) ────────────────────────
        "keyword_exact_match"          : keyword_exact_match,
        "keyword_exact_match_count"    : keyword_exact_match_count,
        "keyword_in_first_100_words"   : keyword_in_first_100_words,
        "keyword_proximity_score"      : keyword_proximity_score,
        "keyword_variations_count"     : keyword_variations_count,
        "tfidf_relevance"              : tfidf_relevance,
        "keyword_prominence_score"     : keyword_prominence_score,

        # ── Semantic relevance (real cosine sim or TF-IDF proxy) ─
        "semantic_relevance"           : semantic_relevance,

        # ── Off-page: OPR (filled by predictor after external APIs)
        "opr_page_rank"            : opr_page_rank,
        "opr_rank_log"             : opr_rank_log,
        "opr_domain_found"         : opr_domain_found,

        # ── Off-page: domain frequency ────────────────────────────
        "domain_frequency"         : domain_frequency,        # raw (not a model input)
        "domain_frequency_log"     : domain_frequency_log,    # log1p- V6 regressor input

        # ── Off-page: Common Crawl (default 0 at inference) ──────
        "cc_pagerank"              : cc_pagerank,
        "cc_harmonic_centrality"   : cc_harmonic_centrality,
        "cc_referring_domains_log" : cc_referring_domains_log,
        "cc_found"                 : cc_found,

        # ── Derived authority ratios ──────────────────────────────
        "relevance_to_authority_ratio" : relevance_to_authority_ratio,
        "semantic_to_authority_ratio"  : semantic_to_authority_ratio,

        # ── Keyword competition / SERP signals ────────────────────
        "keyword_competition"      : keyword_competition,
        "keyword_avg_position"     : keyword_avg_position,
        "keyword_position_std"     : keyword_position_std,

        # ── Lighthouse SEO score (filled by external_apis.py) ────
        "lighthouse_seo_score"     : -1,

        # ── Page text content (for display / extension) ───────────
        "page_title"               : title_text,
        "meta_description"         : meta_content,
        "h1_text"                  : h1_text,
        "canonical_url"            : canonical_url,
        "index_status"             : index_status,
        "body_has_keyword"         : keyword_exact_match,

        # ── Page type (for schema generation) ────────────────────
        "page_type"                : page_type,
        "schema_context"           : schema_context,
    }

    # Merge in query-relative z-score and percentile features (all default 0)
    features.update(_vs_query_z_features)
    features.update(_vs_query_pct_features)

    return features
