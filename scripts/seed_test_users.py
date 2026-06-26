"""
seed_test_users.py
==================
DANGER: wipes ALL Supabase auth users, then creates 4 clean test accounts
(free / pro / agency / business) with confirmed emails and matching
subscription rows.

Run from the backend/ directory:
    python scripts/seed_test_users.py --yes

Without --yes it does a dry run and only lists what it WOULD delete.

Test accounts created:
    freeuser@gmail.com      / FreeUserPass       → free
    prouser@gmail.com       / ProUserPass        → pro
    agencyuser@gmail.com    / AgencyUserPass     → agency
    businessuser@gmail.com  / BusinessUserPass   → business
"""

import sys
import logging
from datetime import datetime, timezone, timedelta

# Allow running from backend/ directory
sys.path.insert(0, ".")

from services.supabase_client import supabase

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed")

# ── Test accounts ─────────────────────────────────────────────────────────────
TEST_USERS = [
    {"email": "freeuser@gmail.com",     "password": "FreeUserPass",     "plan": "free"},
    {"email": "prouser@gmail.com",      "password": "ProUserPass",      "plan": "pro"},
    {"email": "agencyuser@gmail.com",   "password": "AgencyUserPass",   "plan": "agency"},
    {"email": "businessuser@gmail.com", "password": "BusinessUserPass", "plan": "business"},
]

# Tables that reference a user_id — cleaned best-effort before deleting the auth
# user, so no orphan rows remain even if a table lacks ON DELETE CASCADE.
USER_TABLES = [
    "subscriptions",
    "usage_events",
    "audits",
    "api_keys",
    "roadmap_tasks",
    "watched_competitors",
]


def list_all_users() -> list:
    """Page through every auth user."""
    users, page = [], 1
    while True:
        batch = supabase.auth.admin.list_users(page=page, per_page=1000)
        # supabase-py returns a list directly for list_users
        batch = batch if isinstance(batch, list) else getattr(batch, "users", [])
        if not batch:
            break
        users.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    return users


def wipe_all_users(dry_run: bool) -> None:
    users = list_all_users()
    log.info(f"\nFound {len(users)} existing user(s).")
    if not users:
        return

    for u in users:
        uid, email = u.id, u.email
        if dry_run:
            log.info(f"  [dry-run] would delete: {email} ({uid})")
            continue

        # Best-effort: clear linked rows first (covers tables without cascade)
        for table in USER_TABLES:
            try:
                supabase.table(table).delete().eq("user_id", uid).execute()
            except Exception as e:
                log.warning(f"    · {table}: {e}")

        # Delete the auth user (cascades any FK-linked tables too)
        try:
            supabase.auth.admin.delete_user(uid)
            log.info(f"  deleted: {email}")
        except Exception as e:
            log.error(f"  FAILED to delete {email}: {e}")


def create_test_users(dry_run: bool) -> None:
    period_end = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()

    log.info("\nCreating test accounts:")
    for spec in TEST_USERS:
        email, password, plan = spec["email"], spec["password"], spec["plan"]
        if dry_run:
            log.info(f"  [dry-run] would create: {email} → {plan}")
            continue

        # 1. Create the auth user with a pre-confirmed email
        try:
            res = supabase.auth.admin.create_user({
                "email":         email,
                "password":      password,
                "email_confirm": True,
            })
            user = res.user if hasattr(res, "user") else res
            uid = user.id
        except Exception as e:
            log.error(f"  FAILED to create {email}: {e}")
            continue

        # 2. Subscription row. Only write columns the gating logic reads —
        #    plan + status drive everything. (Customer/sub IDs are LS-webhook
        #    bookkeeping, irrelevant for seeded test accounts.)
        sub = {
            "user_id":            uid,
            "plan":               plan,
            "status":             "active",
            "current_period_end": period_end if plan != "free" else None,
            "dev_addon":          False,
        }
        try:
            supabase.table("subscriptions").upsert(sub, on_conflict="user_id").execute()
            log.info(f"  created: {email:<26} → {plan:<9} (pass: {password})")
        except Exception as e:
            log.error(f"  created auth user {email} but subscription FAILED: {e}")


def main() -> None:
    dry_run = "--yes" not in sys.argv

    log.info("=" * 60)
    log.info("SEED TEST USERS" + ("  [DRY RUN — pass --yes to execute]" if dry_run else ""))
    log.info("=" * 60)

    wipe_all_users(dry_run)
    create_test_users(dry_run)

    log.info("\n" + "=" * 60)
    if dry_run:
        log.info("Dry run complete. Re-run with --yes to apply.")
    else:
        log.info("Done. 4 test accounts ready.")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
