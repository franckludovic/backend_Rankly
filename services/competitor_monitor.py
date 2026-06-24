"""
services/competitor_monitor.py
================================
Weekly scheduled job: re-scrapes watched competitor URLs, diffs key metrics
against stored snapshots, and emails the user if significant changes are found.

Thresholds:
  - Title changed (any change triggers alert)
  - Word count shifted ±20%
"""

from __future__ import annotations
import logging
import re
import httpx
from bs4 import BeautifulSoup

from services.email_alerter import send_alert_email, build_alert_html

logger = logging.getLogger(__name__)

_WORD_CHANGE_THRESHOLD = 0.20      # 20 % shift triggers alert
_SCRAPE_TIMEOUT        = 12        # seconds


async def _scrape(url: str) -> tuple[str | None, int]:
    """Returns (title, word_count). Both may be None/0 on failure."""
    try:
        async with httpx.AsyncClient(
            timeout=_SCRAPE_TIMEOUT,
            headers={"User-Agent": "Rankly-Monitor/1.0"},
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
        soup    = BeautifulSoup(r.text, "html.parser")
        title   = soup.title.string.strip() if soup.title and soup.title.string else None
        [tag.decompose() for tag in soup(["script", "style", "nav", "footer", "header"])]
        words   = len(re.findall(r"\w+", soup.get_text(" ", strip=True)))
        return title, words
    except Exception as e:
        logger.warning(f"[competitor_monitor] Scrape failed for {url}: {e}")
        return None, 0


def _detect_changes(watch: dict, new_title: str | None, new_wc: int) -> list[str]:
    changes: list[str] = []

    old_title = watch.get("last_title")
    if old_title and new_title and new_title != old_title:
        changes.append(f"Title changed: &ldquo;{old_title}&rdquo; → &ldquo;{new_title}&rdquo;")

    old_wc = watch.get("last_word_count") or 0
    if old_wc > 0 and new_wc > 0:
        delta_pct = abs(new_wc - old_wc) / old_wc
        if delta_pct >= _WORD_CHANGE_THRESHOLD:
            direction = "increased" if new_wc > old_wc else "decreased"
            changes.append(
                f"Word count {direction} by {round(delta_pct * 100)}% "
                f"({old_wc:,} → {new_wc:,} words)"
            )

    return changes


async def check_all_watches() -> None:
    from storage import competitor_watch_repository as repo

    watches = repo.get_all_watches()
    if not watches:
        logger.info("[competitor_monitor] No watched competitors to check")
        return

    logger.info(f"[competitor_monitor] Checking {len(watches)} watched competitor(s)")

    for watch in watches:
        url     = watch["competitor_url"]
        keyword = watch["keyword"]
        try:
            new_title, new_wc = await _scrape(url)
            changes            = _detect_changes(watch, new_title, new_wc)

            repo.update_snapshot(watch["id"], new_title, new_wc)

            if changes:
                user_email = repo.get_user_email(watch["user_id"])
                if user_email:
                    html    = build_alert_html(url, keyword, changes)
                    subject = f"Competitor change detected: {url}"
                    await send_alert_email(user_email, subject, html)
                    logger.info(f"[competitor_monitor] Alert sent to {user_email} for {url}")
                else:
                    logger.warning(f"[competitor_monitor] Could not resolve email for user {watch['user_id']}")
            else:
                logger.info(f"[competitor_monitor] No changes for {url}")

        except Exception as e:
            logger.error(f"[competitor_monitor] Error processing {url}: {e}")
