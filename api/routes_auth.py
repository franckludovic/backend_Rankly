from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr

from middleware.auth_middleware import get_current_user
from services.email_alerter import send_alert_email, build_welcome_html

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class WelcomeRequest(BaseModel):
    name:  str
    email: str


@router.post("/welcome")
async def send_welcome(body: WelcomeRequest, user_id: str = Depends(get_current_user)):
    """Called by the frontend immediately after a new user registers."""
    html = build_welcome_html(body.name)
    await send_alert_email(
        to      = body.email,
        subject = "Welcome to Rankly- you're all set",
        html    = html,
    )
    return {"sent": True}
