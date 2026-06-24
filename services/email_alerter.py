"""
services/email_alerter.py
==========================
Sends transactional emails via Resend (resend.com).
Free tier: 3,000 emails/month.

Add to .env:
  RESEND_API_KEY=re_...
  RESEND_FROM_EMAIL=alerts@yourdomain.com
"""

import logging
import httpx
from config import RESEND_API_KEY, RESEND_FROM_EMAIL

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"


async def send_alert_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        logger.info(f"[email_alerter] No RESEND_API_KEY- skipping email to {to}")
        return False
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                _RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "from":    RESEND_FROM_EMAIL or "Rankly <alerts@rankly.app>",
                    "to":      [to],
                    "subject": subject,
                    "html":    html,
                },
            )
            resp.raise_for_status()
            logger.info(f"[email_alerter] Sent '{subject}' → {to}")
            return True
    except Exception as e:
        logger.error(f"[email_alerter] Failed to send email to {to}: {e}")
        return False


def build_welcome_html(name: str) -> str:
    first = name.split()[0] if name else "there"
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;background:#f1f5f9;margin:0;padding:32px 16px;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.06);">
    <div style="background:linear-gradient(135deg,#0d9488,#0f766e);padding:28px 28px 24px;">
      <div style="font-size:22px;font-weight:800;color:#fff;letter-spacing:-.3px;">Rank<span style="color:#99f6e4;">ly</span></div>
      <div style="font-size:13px;color:rgba(255,255,255,.7);margin-top:4px;">ML-Powered SEO Auditor</div>
    </div>
    <div style="padding:32px 28px 24px;">
      <p style="font-size:18px;font-weight:700;color:#0f172a;margin:0 0 10px;">Welcome, {first}! Your account is ready.</p>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;line-height:1.7;">
        You&#39;re now on the <strong style="color:#0d9488;">Free plan</strong>- 3 full SEO audits per month.
        Each audit runs 70+ on-page checks, scrapes your top 10 competitors from live SERP data,
        and gives you an ML-powered rank prediction.
      </p>
      <div style="background:#f8fafc;border-radius:10px;padding:18px 20px;margin-bottom:24px;">
        <p style="font-size:12px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:.6px;margin:0 0 12px;">What to do first</p>
        <div style="font-size:13.5px;color:#334155;line-height:1.8;">
          ① Go to your <strong>Dashboard</strong><br>
          ② Paste any URL you want to rank higher<br>
          ③ Enter the target keyword<br>
          ④ Hit <strong>Run Audit</strong>- results in &lt;30 seconds
        </div>
      </div>
      <a href="https://rankly.app/dashboard"
         style="display:inline-block;background:#0d9488;color:#fff;padding:12px 24px;border-radius:9px;text-decoration:none;font-size:14px;font-weight:700;">
        Run Your First Audit →
      </a>
    </div>
    <div style="padding:16px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        Need help? Just reply to this email- we read every message.
      </p>
    </div>
  </div>
</body>
</html>"""


def build_upgrade_html(name: str, plan: str, plan_limit: int) -> str:
    first   = name.split()[0] if name else "there"
    plan_cap = plan.capitalize()
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;background:#f1f5f9;margin:0;padding:32px 16px;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.06);">
    <div style="background:linear-gradient(135deg,#0d9488,#0f766e);padding:28px 28px 24px;">
      <div style="font-size:22px;font-weight:800;color:#fff;letter-spacing:-.3px;">Rank<span style="color:#99f6e4;">ly</span></div>
      <div style="font-size:13px;color:rgba(255,255,255,.7);margin-top:4px;">You&#39;re now on {plan_cap}</div>
    </div>
    <div style="padding:32px 28px 24px;">
      <p style="font-size:18px;font-weight:700;color:#0f172a;margin:0 0 10px;">
        You&#39;re on {plan_cap}, {first}!
      </p>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;line-height:1.7;">
        Your account is now upgraded to the <strong style="color:#0d9488;">{plan_cap} plan</strong>
        with <strong>{plan_limit:,} audits per month</strong>.
        All premium features are now unlocked.
      </p>
      <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:18px 20px;margin-bottom:24px;">
        <p style="font-size:12px;font-weight:700;color:#166534;text-transform:uppercase;letter-spacing:.6px;margin:0 0 10px;">Now unlocked</p>
        <div style="font-size:13.5px;color:#15803d;line-height:1.9;">
          ✓ {plan_limit:,} audits / month<br>
          ✓ PDF report export<br>
          ✓ AI content briefs (Gemini)<br>
          ✓ A/B title scorer<br>
          ✓ Competitor change alerts
        </div>
      </div>
      <a href="https://rankly.app/dashboard"
         style="display:inline-block;background:#0d9488;color:#fff;padding:12px 24px;border-radius:9px;text-decoration:none;font-size:14px;font-weight:700;">
        Back to Dashboard →
      </a>
    </div>
    <div style="padding:16px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        Manage your subscription anytime from the Billing page in your dashboard.
      </p>
    </div>
  </div>
</body>
</html>"""


def build_alert_html(competitor_url: str, keyword: str, changes: list[str]) -> str:
    changes_html = "".join(
        f'<li style="margin-bottom:8px;color:#334155;">{c}</li>' for c in changes
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;background:#f1f5f9;margin:0;padding:32px 16px;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.06);">
    <div style="background:#0d9488;padding:24px 28px;">
      <div style="font-size:20px;font-weight:800;color:#fff;letter-spacing:-.3px;">Rank<span style="color:#ccfbf1;">ly</span></div>
      <div style="font-size:13px;color:rgba(255,255,255,.7);margin-top:4px;">Competitor Change Alert</div>
    </div>
    <div style="padding:28px;">
      <p style="font-size:15px;font-weight:700;color:#0f172a;margin:0 0 6px;">A competitor you&#39;re monitoring has changed</p>
      <p style="font-size:13px;color:#64748b;margin:0 0 20px;line-height:1.6;">
        We detected the following changes for <strong style="color:#0d9488;">{competitor_url}</strong>
        (keyword: <em>{keyword}</em>):
      </p>
      <ul style="margin:0 0 24px;padding-left:20px;font-size:13.5px;line-height:1.7;">
        {changes_html}
      </ul>
      <a href="{competitor_url}" style="display:inline-block;background:#0d9488;color:#fff;padding:10px 22px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:600;">
        View Competitor Page →
      </a>
    </div>
    <div style="padding:16px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        You&#39;re receiving this because you&#39;re monitoring this competitor in Rankly.
        Manage your alerts in your dashboard.
      </p>
    </div>
  </div>
</body>
</html>"""
