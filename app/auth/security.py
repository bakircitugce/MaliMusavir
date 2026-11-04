import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.config import get_settings

SETTINGS = get_settings()
AUTH_SECRET = getattr(SETTINGS, "auth_secret", "mali_asistan_super_secure_key_2026_smmm")


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a cryptographically secure random salt."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    )
    return f"{salt}:{dk.hex()}"


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    """Verify a plain password against a hashed password."""
    if not hashed_password or ":" not in hashed_password:
        return False
    try:
        salt, dk_hex = hashed_password.split(":", 1)
        test_dk = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        )
        return secrets.compare_digest(test_dk.hex(), dk_hex)
    except Exception:
        return False


def create_token(user_id: str, email: str, rol: str, buro_id: str, expires_in_seconds: int = 86400 * 7) -> str:
    """Create a signed JWT-like bearer token valid for a specified duration."""
    exp = int(time.time()) + expires_in_seconds
    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "rol": rol,
        "buro_id": buro_id,
        "exp": exp,
    }
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii").rstrip("=")

    signature = hmac.new(
        AUTH_SECRET.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    return f"{payload_b64}.{signature}"


def decode_token(token: str) -> dict[str, Any] | None:
    """Validate and decode a bearer token. Returns payload dict or None if invalid or expired."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, signature = token.split(".", 1)

        expected_sig = hmac.new(
            AUTH_SECRET.encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

        if not secrets.compare_digest(expected_sig, signature):
            return None

        # Add padding back if necessary
        padded_b64 = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload_json = base64.urlsafe_b64decode(padded_b64.encode("ascii")).decode("utf-8")
        payload = json.loads(payload_json)

        if "exp" in payload and payload["exp"] < int(time.time()):
            return None

        return payload
    except Exception:
        return None
