from fastapi import APIRouter, Depends, HTTPException, status
from middleware.auth_middleware import get_current_user
from schemas.usage import UsageCheckRequest, UsageCheckResponse, UsageConsumeRequest, UsageConsumeResponse
from services.quota_service import QuotaService, UsageLimitError

router = APIRouter(prefix="/api/usage", tags=["Usage"])

@router.get("/me")
async def get_usage_me(user_id: str = Depends(get_current_user)):
    main_app = QuotaService.check_quota("user", user_id, "main_app", "online")
    ext_online = QuotaService.check_quota("user", user_id, "extension", "online")
    
    return {
        "main_app": {
            "used": main_app["limit"] - main_app["remaining"],
            "limit": main_app["limit"],
            "remaining": main_app["remaining"]
        },
        "extension_online": {
            "used": ext_online["limit"] - ext_online["remaining"],
            "limit": ext_online["limit"],
            "remaining": ext_online["remaining"]
        },
        "extension_offline": {
            "used": 0,
            "limit": QuotaService.get_limit("extension", "offline"),
            "remaining": QuotaService.get_limit("extension", "offline")
        }
    }

@router.post("/check", response_model=UsageCheckResponse)
async def check_usage(body: UsageCheckRequest):
    res = QuotaService.check_quota(
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        product=body.product,
        mode=body.mode
    )
    return UsageCheckResponse(
        allowed=res["allowed"],
        remaining=res["remaining"],
        limit=res["limit"]
    )

@router.post("/consume", response_model=UsageConsumeResponse)
async def consume_usage(body: UsageConsumeRequest):
    try:
        res = QuotaService.check_and_consume(
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            product=body.product,
            mode=body.mode,
            idempotency_key=body.idempotency_key,
            audit_id=body.audit_id
        )
        return UsageConsumeResponse(
            consumed=res["consumed"],
            already_consumed=res["already_consumed"],
            remaining=res["remaining"]
        )
    except UsageLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": e.message,
                "code": e.code,
                "usage": {
                    "remaining": e.remaining,
                    "limit": e.limit
                }
            }
        )
