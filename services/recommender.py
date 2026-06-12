"""
services/recommender.py
========================
Generates prioritised, actionable SEO recommendations
based on extracted features and predicted quality.
Data-driven thresholds from your training data.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class Recommendation:
    priority : int
    impact   : str    # HIGH / MEDIUM / LOW
    category : str    # Title / Meta / Content / Technical / Images / Links
    issue    : str
    action   : str
    metric   : str    # current value for display


def generate_recommendations(features: dict, quality: str) -> List[dict]:
    """
    Generate prioritised SEO recommendations.
    Returns list of recommendations sorted by priority (1 = most urgent).
    """
    recs = []

    # ── CRITICAL — Missing elements ────────────────────────────

    if features.get("meta_desc_present", 1) == 0:
        recs.append(Recommendation(
            priority=1, impact="HIGH", category="Meta",
            issue="Missing meta description",
            action="Add a meta description between 120–160 characters "
                   "that includes your target keyword",
            metric="Not present"
        ))

    if features.get("h1_count", 1) == 0:
        recs.append(Recommendation(
            priority=1, impact="HIGH", category="Headings",
            issue="No H1 heading found",
            action="Add exactly one H1 heading that includes your target keyword",
            metric="0 H1 tags"
        ))

    if features.get("title_has_keyword", 1) == 0:
        recs.append(Recommendation(
            priority=2, impact="HIGH", category="Title",
            issue="Target keyword missing from title tag",
            action="Include your target keyword in the page title, "
                   "ideally near the beginning",
            metric=f"Title: '{features.get('title_length', 0)} chars, no keyword'"
        ))

    # ── CONTENT issues ─────────────────────────────────────────

    word_count = features.get("word_count", 0)
    if word_count < 300:
        recs.append(Recommendation(
            priority=2, impact="HIGH", category="Content",
            issue=f"Thin content — only {word_count} words",
            action="Expand content to at least 600 words. "
                   "High-quality pages in this dataset average 847 words.",
            metric=f"{word_count} words (target: 600+)"
        ))
    elif word_count < 500:
        recs.append(Recommendation(
            priority=4, impact="MEDIUM", category="Content",
            issue=f"Content could be more comprehensive ({word_count} words)",
            action="Consider expanding to 700+ words with additional detail "
                   "and examples",
            metric=f"{word_count} words"
        ))

    kw_density = features.get("keyword_density", 0)
    if kw_density < 0.3 and word_count >= 300:
        recs.append(Recommendation(
            priority=4, impact="MEDIUM", category="Content",
            issue=f"Very low keyword density ({kw_density:.2f}%)",
            action="Naturally increase keyword usage. Aim for 0.5–2% density "
                   "without forcing it.",
            metric=f"{kw_density:.2f}% (target: 0.5–2%)"
        ))
    elif kw_density > 4.0:
        recs.append(Recommendation(
            priority=3, impact="MEDIUM", category="Content",
            issue=f"Keyword stuffing detected ({kw_density:.2f}%)",
            action="Reduce keyword repetition. Over-optimisation can trigger "
                   "Google penalties. Aim for 0.5–2%.",
            metric=f"{kw_density:.2f}% (target: 0.5–2%)"
        ))

    # ── TITLE issues ───────────────────────────────────────────

    title_length = features.get("title_length", 0)
    if title_length == 0:
        recs.append(Recommendation(
            priority=1, impact="HIGH", category="Title",
            issue="Missing title tag",
            action="Add a title tag between 50–60 characters including "
                   "your target keyword",
            metric="No title tag found"
        ))
    elif title_length < 30:
        recs.append(Recommendation(
            priority=3, impact="MEDIUM", category="Title",
            issue=f"Title tag too short ({title_length} chars)",
            action="Expand title to 50–60 characters. Short titles "
                   "miss opportunity to include important keywords.",
            metric=f"{title_length} chars (target: 50–60)"
        ))
    elif title_length > 70:
        recs.append(Recommendation(
            priority=4, impact="LOW", category="Title",
            issue=f"Title tag too long ({title_length} chars)",
            action="Shorten title to under 60 characters. Google truncates "
                   "titles over ~60 chars in search results.",
            metric=f"{title_length} chars (target: under 60)"
        ))

    # ── META DESCRIPTION quality ───────────────────────────────

    if features.get("meta_desc_present", 0) == 1:
        meta_len = features.get("meta_desc_length", 0)
        if meta_len < 120:
            recs.append(Recommendation(
                priority=3, impact="MEDIUM", category="Meta",
                issue=f"Meta description too short ({meta_len} chars)",
                action="Expand to 120–160 characters. Short descriptions "
                       "get truncated and miss keyword opportunities.",
                metric=f"{meta_len} chars (target: 120–160)"
            ))
        elif meta_len > 165:
            recs.append(Recommendation(
                priority=4, impact="LOW", category="Meta",
                issue=f"Meta description too long ({meta_len} chars)",
                action="Shorten to under 160 characters to avoid truncation "
                       "in search results.",
                metric=f"{meta_len} chars (target: under 160)"
            ))

        if features.get("meta_desc_has_keyword", 1) == 0:
            recs.append(Recommendation(
                priority=4, impact="MEDIUM", category="Meta",
                issue="Target keyword missing from meta description",
                action="Include your target keyword naturally in the "
                       "meta description — improves click-through rate.",
                metric="Keyword absent from description"
            ))

    # ── IMAGES ─────────────────────────────────────────────────

    img_count = features.get("image_count", 0)
    alt_count = features.get("images_with_alt_count", 0)
    if img_count > 0 and alt_count < img_count:
        missing = img_count - alt_count
        recs.append(Recommendation(
            priority=3, impact="MEDIUM", category="Images",
            issue=f"{missing} image(s) missing alt text",
            action=f"Add descriptive alt text to all {img_count} images. "
                   "Include your keyword in at least one image alt attribute.",
            metric=f"{alt_count}/{img_count} images have alt text"
        ))

    # ── TECHNICAL ──────────────────────────────────────────────

    if features.get("has_schema_markup", 1) == 0:
        recs.append(Recommendation(
            priority=5, impact="MEDIUM", category="Technical",
            issue="No structured data (schema markup)",
            action="Add Schema.org markup appropriate for your content "
                   "(Article, Product, FAQPage, etc.). "
                   "Enables rich snippets in search results.",
            metric="No JSON-LD found"
        ))

    if features.get("has_canonical_tag", 1) == 0:
        recs.append(Recommendation(
            priority=5, impact="MEDIUM", category="Technical",
            issue="Missing canonical tag",
            action="Add <link rel='canonical' href='...'> to prevent "
                   "duplicate content issues.",
            metric="No canonical tag found"
        ))

    if features.get("has_og_tags", 1) == 0:
        recs.append(Recommendation(
            priority=6, impact="LOW", category="Social",
            issue="Missing Open Graph tags",
            action="Add og:title, og:description, og:image meta tags "
                   "to improve appearance when shared on social media.",
            metric="No OG tags found"
        ))

    # ── LINKS ──────────────────────────────────────────────────

    int_links = features.get("internal_link_count", 0)
    if int_links < 3:
        recs.append(Recommendation(
            priority=5, impact="MEDIUM", category="Links",
            issue=f"Very few internal links ({int_links})",
            action="Add 3–5 internal links to related pages. "
                   "Internal linking distributes page authority "
                   "and helps search engines discover content.",
            metric=f"{int_links} internal links (target: 3+)"
        ))

    # ── H1 quality ─────────────────────────────────────────────

    if features.get("h1_count", 0) > 0 and features.get("h1_has_keyword", 1) == 0:
        recs.append(Recommendation(
            priority=4, impact="MEDIUM", category="Headings",
            issue="Target keyword missing from H1 heading",
            action="Include your target keyword in the H1 heading. "
                   "H1 is the strongest on-page keyword signal.",
            metric="H1 present but keyword absent"
        ))

    if features.get("h1_count", 0) > 1:
        recs.append(Recommendation(
            priority=5, impact="LOW", category="Headings",
            issue=f"Multiple H1 tags found ({features.get('h1_count')})",
            action="Use only one H1 per page. Use H2/H3 for subsections.",
            metric=f"{features.get('h1_count')} H1 tags found"
        ))

    # Sort by priority (1 = most urgent)
    recs.sort(key=lambda r: r.priority)

    return [
        {
            "priority": r.priority,
            "impact"  : r.impact,
            "category": r.category,
            "issue"   : r.issue,
            "action"  : r.action,
            "metric"  : r.metric,
        }
        for r in recs
    ]
