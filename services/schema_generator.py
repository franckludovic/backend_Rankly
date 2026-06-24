"""
services/schema_generator.py
============================
Generates JSON-LD schema markup based on the page type detected during feature extraction.
Returns copy-paste ready code with placeholder comments for values the server cannot know
(author name, price, business address, etc.).
"""

import json


def generate_schema(
    page_type: str,
    features: dict,
    url: str = "",
    keyword: str = "",
    context: dict = None,
) -> dict:
    """
    Build JSON-LD schema for the given page_type.
    Returns {"type": str, "json_ld": str, "script_tag": str}.
    """
    ctx    = context or {}
    title  = features.get("page_title") or keyword or "Page Title"
    meta   = features.get("meta_description") or ""
    schema = {"@context": "https://schema.org"}

    if page_type == "Product":
        schema.update({
            "@type": "Product",
            "name": title,
            "description": meta or f"Find the best {keyword}.",
            "url": url,
            "offers": {
                "@type": "Offer",
                "priceCurrency": "USD",
                "price": "<!-- ADD PRICE -->",
                "availability": "https://schema.org/InStock",
                "url": url,
            },
        })

    elif page_type == "FAQPage":
        questions = ctx.get("questions") or [
            f"What is {keyword}?",
            f"How does {keyword} work?",
            f"What are the benefits of {keyword}?",
        ]
        schema.update({
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "<!-- ADD ANSWER -->",
                    },
                }
                for q in questions
            ],
        })

    elif page_type == "HowTo":
        steps = ctx.get("steps") or ["<!-- Step 1 -->", "<!-- Step 2 -->", "<!-- Step 3 -->"]
        schema.update({
            "@type": "HowTo",
            "name": title,
            "description": meta,
            "step": [{"@type": "HowToStep", "text": s} for s in steps],
        })

    elif page_type == "Article":
        schema.update({
            "@type": "Article",
            "headline": title[:110],
            "description": meta,
            "url": url,
            "author": {"@type": "Person", "name": "<!-- AUTHOR NAME -->"},
            "publisher": {
                "@type": "Organization",
                "name": "<!-- PUBLISHER NAME -->",
                "logo": {"@type": "ImageObject", "url": "<!-- LOGO URL -->"},
            },
            "datePublished": "<!-- YYYY-MM-DD -->",
            "dateModified": "<!-- YYYY-MM-DD -->",
        })

    elif page_type == "LocalBusiness":
        schema.update({
            "@type": "LocalBusiness",
            "name": "<!-- BUSINESS NAME -->",
            "description": meta,
            "url": url,
            "telephone": "<!-- +1-XXX-XXX-XXXX -->",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "<!-- Street Address -->",
                "addressLocality": "<!-- City -->",
                "addressRegion": "<!-- State -->",
                "postalCode": "<!-- ZIP -->",
                "addressCountry": "US",
            },
        })

    else:  # WebPage
        schema.update({
            "@type": "WebPage",
            "name": title,
            "description": meta,
            "url": url,
        })

    json_ld    = json.dumps(schema, indent=2)
    script_tag = f'<script type="application/ld+json">\n{json_ld}\n</script>'

    return {
        "type": page_type,
        "json_ld": json_ld,
        "script_tag": script_tag,
    }
