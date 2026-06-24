from fastapi import Header, HTTPException
from config import SUPABASE_JWT_SECRET
import jwt

_API_KEY_PREFIX = "rkly_"


async def get_current_user(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ").strip()

    # ── API key path (rkly_*) ──────────────────────────────────────
    if token.startswith(_API_KEY_PREFIX):
        from storage.api_key_repository import get_user_id_for_key
        user_id = get_user_id_for_key(token)
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        return user_id

    # ── Supabase JWT path- verified locally, no network call ─────
    if not SUPABASE_JWT_SECRET:
        # Fallback: call Supabase auth API if JWT secret not configured
        from services.supabase_client import supabase
        try:
            user_response = supabase.auth.get_user(token)
            return str(user_response.user.id)
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid or expired token: {str(e)}")

    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token missing subject claim")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
