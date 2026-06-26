"""
Lemon Squeezy billing integration.

pip install httpx

Flow:
  1. Frontend calls POST /api/billing/checkout → gets {url}
  2. Browser redirects to Lemon Squeezy hosted checkout
  3. User pays
  4. LS redirects to /billing/success and fires a webhook
  5. Webhook updates the subscriptions table in Supabase
  6. quota_service.py reads the plan on every audit request
"""

import hmac
import hashlib
import json
import logging
import httpx

from config import (
    LS_API_KEY,
    LS_STORE_ID,
    LS_WEBHOOK_SECRET,
    LS_VARIANT_PRO,
    LS_VARIANT_AGENCY,
    LS_VARIANT_BUSINESS,
    LS_VARIANT_DEV_ADDON,
    APP_BASE_URL,
)
from storage import subscription_repository as sub_repo
from services.email_alerter import send_alert_email, build_upgrade_html

logger = logging.getLogger(__name__)

LS_BASE = "https://api.lemonsqueezy.com/v1"

PLAN_VARIANT_MAP: dict[str, str] = {
    "pro":       LS_VARIANT_PRO,
    "agency":    LS_VARIANT_AGENCY,
    "business":  LS_VARIANT_BUSINESS,
    "dev_addon": LS_VARIANT_DEV_ADDON,  # Pro add-on: unlocks API key access
}


def _variant_to_plan() -> dict[str, str]:
    return {v: k for k, v in PLAN_VARIANT_MAP.items() if v}


def _headers() -> dict:
    return {
        "Authorization":  f"Bearer {LS_API_KEY}",
        "Accept":         "application/vnd.api+json",
        "Content-Type":   "application/vnd.api+json",
    }


# ── Public API ─────────────────────────────────────────────────────────────────

async def create_checkout_session(user_id: str, user_email: str, plan: str) -> str:
    """Create a Lemon Squeezy checkout and return the hosted checkout URL."""
    variant_id = PLAN_VARIANT_MAP.get(plan)
    if not variant_id:
        raise ValueError(f"Unknown plan: '{plan}'. Valid plans: pro, agency, business, dev_addon.")
    if not LS_API_KEY or not LS_STORE_ID:
        raise RuntimeError("LS_API_KEY or LS_STORE_ID is not configured.")

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email":  user_email,
                    "custom": {"user_id": user_id},
                },
                "product_options": {
                    "redirect_url": f"{APP_BASE_URL}/billing/success",
                },
            },
            "relationships": {
                "store":   {"data": {"type": "stores",   "id": str(LS_STORE_ID)}},
                "variant": {"data": {"type": "variants", "id": str(variant_id)}},
            },
        }
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{LS_BASE}/checkouts", json=payload, headers=_headers())

    if resp.status_code != 201:
        logger.error(f"LS checkout error {resp.status_code}: {resp.text}")
        raise RuntimeError(f"Lemon Squeezy error: {resp.status_code}")

    return resp.json()["data"]["attributes"]["url"]


async def create_portal_session(user_id: str) -> str:
    """Return the Lemon Squeezy customer portal URL for this user's subscription."""
    sub = sub_repo.get_subscription(user_id)
    if not sub or not sub.get("ls_subscription_id"):
        raise ValueError("No active subscription found. Upgrade to a paid plan first.")

    ls_sub_id = sub["ls_subscription_id"]
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{LS_BASE}/subscriptions/{ls_sub_id}", headers=_headers())

    if resp.status_code != 200:
        raise RuntimeError(f"Could not fetch subscription from Lemon Squeezy: {resp.status_code}")

    portal_url = resp.json()["data"]["attributes"].get("urls", {}).get("customer_portal")
    if not portal_url:
        raise ValueError("Customer portal URL is not available.")

    return portal_url


async def handle_webhook_event(payload: bytes, signature: str) -> dict:
    """Verify and process a Lemon Squeezy webhook event."""
    _verify_signature(payload, signature)

    event      = json.loads(payload)
    event_name = event.get("meta", {}).get("event_name", "")
    logger.info(f"Lemon Squeezy webhook: {event_name}")

    if event_name in (
        "subscription_created",
        "subscription_updated",
        "subscription_payment_success",
        "subscription_resumed",
    ):
        await _handle_active(event)

    elif event_name in ("subscription_cancelled", "subscription_expired"):
        _handle_cancelled(event)

    elif event_name == "subscription_payment_failed":
        _handle_payment_failed(event)

    return {"received": True}


# ── Webhook handlers ───────────────────────────────────────────────────────────

async def _handle_active(event: dict) -> None:
    data   = event.get("data", {})
    attrs  = data.get("attributes", {})
    meta   = event.get("meta", {})

    user_id = meta.get("custom_data", {}).get("user_id")
    if not user_id:
        logger.warning(f"Webhook missing user_id in custom_data: {meta.get('event_name')}")
        return

    variant_id     = str(attrs.get("variant_id", ""))
    ls_sub_id      = str(data.get("id", ""))
    ls_customer_id = str(attrs.get("customer_id", ""))
    status         = attrs.get("status", "active")
    renews_at      = attrs.get("renews_at")

    if LS_VARIANT_DEV_ADDON and variant_id == str(LS_VARIANT_DEV_ADDON):
        # Developer Add-on purchased — enable API access without touching the main plan
        sub_repo.upsert_subscription(user_id, {
            "dev_addon":                    True,
            "ls_dev_addon_subscription_id": ls_sub_id,
        })
        logger.info(f"User {user_id} → dev_addon enabled")
        return

    plan = _variant_to_plan().get(variant_id, "free")
    sub_repo.upsert_subscription(user_id, {
        "ls_subscription_id": ls_sub_id,
        "ls_customer_id":     ls_customer_id,
        "plan":               plan,
        "status":             status,
        "current_period_end": renews_at,
    })
    logger.info(f"User {user_id} → plan={plan}  status={status}")

    # Send upgrade confirmation email (fire and forget- never block the webhook)
    if event_name == "subscription_created":
        email     = attrs.get("user_email")
        full_name = attrs.get("user_name", "")
        limit     = {"pro": 50, "agency": 500, "business": 10_000}.get(plan, 50)
        if email:
            try:
                await send_alert_email(
                    to      = email,
                    subject = f"You're now on Rankly {plan.capitalize()}- welcome aboard",
                    html    = build_upgrade_html(full_name, plan, limit),
                )
            except Exception as exc:
                logger.warning(f"Upgrade email failed for {email}: {exc}")


def _handle_cancelled(event: dict) -> None:
    data      = event.get("data", {})
    attrs     = data.get("attributes", {})
    meta      = event.get("meta", {})
    ls_sub_id = str(data.get("id", ""))

    user_id = meta.get("custom_data", {}).get("user_id")

    # Check if this is a dev_addon cancellation
    if LS_VARIANT_DEV_ADDON:
        addon_record = sub_repo.get_by_ls_dev_addon_subscription(ls_sub_id)
        if addon_record:
            sub_repo.upsert_subscription(addon_record["user_id"], {
                "dev_addon":                    False,
                "ls_dev_addon_subscription_id": None,
            })
            logger.info(f"User {addon_record['user_id']} dev_addon cancelled → revoked")
            return

    if not user_id:
        record = sub_repo.get_by_ls_subscription(ls_sub_id)
        if record:
            user_id = record["user_id"]
    if not user_id:
        logger.warning(f"Cancelled webhook: could not find user for sub {ls_sub_id}")
        return

    sub_repo.upsert_subscription(user_id, {
        "ls_subscription_id": ls_sub_id,
        "plan":               "free",
        "status":             "cancelled",
        "current_period_end": attrs.get("ends_at"),
    })
    logger.info(f"User {user_id} cancelled → free")


def _handle_payment_failed(event: dict) -> None:
    data      = event.get("data", {})
    ls_sub_id = str(data.get("id", ""))
    record    = sub_repo.get_by_ls_subscription(ls_sub_id)
    if not record:
        return
    sub_repo.upsert_subscription(record["user_id"], {
        "ls_subscription_id": ls_sub_id,
        "status": "past_due",
    })
    logger.warning(f"Payment failed for user {record['user_id']}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str) -> None:
    if not LS_WEBHOOK_SECRET:
        logger.warning("LS_WEBHOOK_SECRET not set- skipping webhook verification")
        return
    expected = hmac.new(
        LS_WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise ValueError("Invalid Lemon Squeezy webhook signature.")
