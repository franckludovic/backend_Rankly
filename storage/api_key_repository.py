"""
storage/api_key_repository.py
================================
CRUD for the api_keys table.
Keys are stored as SHA-256 hashes; the plaintext key is only returned at creation.
"""

from __future__ import annotations
import hashlib
import logging
import os
from datetime import datetime, timezone

from services.supabase_client import supabase

logger = logging.getLogger(__name__)

_PREFIX = "rkly_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> str:
    """Returns a new plaintext API key.  Only ever shown once."""
    return _PREFIX + os.urandom(20).hex()   # "rkly_" + 40 hex chars


def list_keys(user_id: str) -> list[dict]:
    rows = (
        supabase.table("api_keys")
        .select("id,name,key_prefix,created_at,last_used_at,revoked")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
        .data or []
    )
    return rows


def create_key(user_id: str, name: str) -> dict:
    """Create a new key and return {key, record}.  `key` is shown only here."""
    plaintext = generate_key()
    record = {
        "user_id":    user_id,
        "name":       name,
        "key_hash":   _hash(plaintext),
        "key_prefix": plaintext[:12],
    }
    result = supabase.table("api_keys").insert(record).execute()
    row    = (result.data or [{}])[0]
    return {"key": plaintext, "record": row}


def revoke_key(key_id: str, user_id: str) -> bool:
    supabase.table("api_keys").update({"revoked": True}).eq("id", key_id).eq("user_id", user_id).execute()
    return True


def get_user_id_for_key(plaintext: str) -> str | None:
    """Looks up a plaintext key by hash.  Returns user_id or None."""
    h = _hash(plaintext)
    rows = (
        supabase.table("api_keys")
        .select("id,user_id")
        .eq("key_hash", h)
        .eq("revoked", False)
        .execute()
        .data or []
    )
    if not rows:
        return None
    row = rows[0]
    _touch_last_used(row["id"])
    return row["user_id"]


def _touch_last_used(key_id: str) -> None:
    try:
        supabase.table("api_keys").update({
            "last_used_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", key_id).execute()
    except Exception as e:
        logger.debug(f"Could not update last_used_at for {key_id}: {e}")
