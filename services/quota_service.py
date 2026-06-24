from datetime import datetime, timezone
from storage import usage_repository as usage_repo

# Monthly audit limits per Stripe plan
PLAN_LIMITS = {
    "free":     5,
    "pro":      50,
    "agency":   500,
    "business": 10_000,
}

# Non-plan limits (extension usage etc.)
_FIXED_LIMITS = {
    ("extension", "offline"): 100,
    ("extension", "online"):   30,
}


class UsageLimitError(Exception):
    def __init__(self, code, message, remaining=0, limit=50):
        self.code = code
        self.message = message
        self.remaining = remaining
        self.limit = limit
        super().__init__(message)


class QuotaService:

    @classmethod
    def get_limit(cls, product: str, mode: str, user_id: str | None = None) -> int:
        if product == "main_app" and mode == "online" and user_id:
            from storage.subscription_repository import get_plan
            plan = get_plan(user_id)
            return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        return _FIXED_LIMITS.get((product, mode), PLAN_LIMITS["free"])

    @classmethod
    def _month_start(cls) -> str:
        now = datetime.now(timezone.utc)
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    @classmethod
    def check_quota(cls, subject_type: str, subject_id: str, product: str, mode: str) -> dict:
        user_id = subject_id if subject_type == "user" else None
        limit   = cls.get_limit(product, mode, user_id)
        used    = usage_repo.count_events_this_month(
            subject_type, subject_id, product, mode, cls._month_start()
        )
        remaining = max(0, limit - used)
        return {"allowed": remaining > 0, "remaining": remaining, "limit": limit}

    @classmethod
    def check_and_consume(
        cls,
        subject_type: str,
        subject_id: str,
        product: str,
        mode: str,
        idempotency_key: str,
        audit_id: str = None,
    ) -> dict:
        if usage_repo.check_idempotency(idempotency_key):
            quota = cls.check_quota(subject_type, subject_id, product, mode)
            return {"consumed": False, "already_consumed": True, "remaining": quota["remaining"]}

        user_id = subject_id if subject_type == "user" else None
        limit   = cls.get_limit(product, mode, user_id)
        used    = usage_repo.count_events_this_month(
            subject_type, subject_id, product, mode, cls._month_start()
        )

        if used >= limit:
            raise UsageLimitError(
                code="MONTHLY_LIMIT_REACHED",
                message=f"Monthly limit of {limit} reached. Upgrade your plan for more audits.",
                remaining=0,
                limit=limit,
            )

        usage_repo.record_event(idempotency_key, subject_type, subject_id, product, mode, audit_id)

        return {"consumed": True, "already_consumed": False, "remaining": max(0, limit - used - 1)}
