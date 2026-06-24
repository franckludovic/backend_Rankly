import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from middleware.auth_middleware import get_current_user
from services.billing_service import (
    create_checkout_session,
    create_portal_session,
    handle_webhook_event,
)
from storage import subscription_repository as sub_repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["Billing"])


class CheckoutRequest(BaseModel):
    plan:  str   # "pro" | "agency" | "business"
    email: str   # user's email- already in auth state on the frontend


@router.post("/checkout")
async def checkout(body: CheckoutRequest, user_id: str = Depends(get_current_user)):
    """Create a Lemon Squeezy checkout session. Returns {url} to redirect the browser to."""
    try:
        url = await create_checkout_session(user_id, body.email, body.plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Checkout session creation failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"url": url}


@router.get("/portal")
async def portal(user_id: str = Depends(get_current_user)):
    """Return the Lemon Squeezy customer portal URL so the user can manage their subscription."""
    try:
        url = await create_portal_session(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Portal session creation failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"url": url}


@router.get("/subscription")
async def get_subscription(user_id: str = Depends(get_current_user)):
    """Return the current user's plan and subscription status."""
    sub = sub_repo.get_subscription(user_id)
    if not sub:
        return {"plan": "free", "status": "active", "current_period_end": None}
    return {
        "plan":               sub.get("plan", "free"),
        "status":             sub.get("status", "active"),
        "current_period_end": sub.get("current_period_end"),
    }


@router.post("/webhook")
async def ls_webhook(request: Request):
    """
    Lemon Squeezy webhook- no JWT auth, verified by X-Signature header.
    Register this URL in your LS dashboard → Settings → Webhooks.
    """
    payload   = await request.body()
    signature = request.headers.get("X-Signature", "")
    try:
        result = await handle_webhook_event(payload, signature)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Webhook processing error")
        raise HTTPException(status_code=500, detail=str(e))
    return result
