"""Şifre özetleme ve erişim token'ı (Faz 0.1 / 0.5).

- Şifreler PBKDF2-HMAC-SHA256 ile, en az 600.000 iterasyonla özetlenir.
  Biçim: ``pbkdf2_sha256$<iterasyon>$<salt>$<hex>``. Eski biçim (``salt:hex``,
  100.000 iterasyon) yalnızca doğrulama için okunur ve girişte yeniden özetlenir.
- Token'lar PyJWT ile HS256 imzalanır; ``exp``, ``iat``, ``sub``, ``buro_id`` ve
  ``tv`` (token_version) zorunludur. Algoritma sabittir (``alg=none`` kabul edilmez).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings

_ALGORITHM = "HS256"
_SCHEME = "pbkdf2_sha256"
_LEGACY_ITERATIONS = 100_000
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "buro_id", "tv"]


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()


def hash_password(password: str) -> str:
    iterations = get_settings().password_hash_iterations
    salt = secrets.token_hex(16)
    return f"{_SCHEME}${iterations}${salt}${_pbkdf2(password, salt, iterations)}"


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    try:
        if hashed_password.startswith(f"{_SCHEME}$"):
            _, iterations, salt, expected = hashed_password.split("$", 3)
            actual = _pbkdf2(plain_password, salt, int(iterations))
        elif ":" in hashed_password:
            salt, expected = hashed_password.split(":", 1)
            actual = _pbkdf2(plain_password, salt, _LEGACY_ITERATIONS)
        else:
            return False
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def needs_rehash(hashed_password: str | None) -> bool:
    """Eski biçim veya güncel ayardan düşük iterasyonlu özetler yenilenmelidir."""
    if not hashed_password or not hashed_password.startswith(f"{_SCHEME}$"):
        return True
    try:
        iterations = int(hashed_password.split("$", 2)[1])
    except (IndexError, ValueError):
        return True
    return iterations < get_settings().password_hash_iterations


# Var olmayan kullanıcı için de aynı maliyette doğrulama yapıp yanıt süresinden
# kullanıcı varlığının sızmasını önlemek amacıyla kullanılır.
_DUMMY_HASH: str | None = None


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _DUMMY_HASH)


def _secret() -> str:
    return get_settings().auth_secret.get_secret_value()


def create_token(
    *,
    user_id: str,
    buro_id: str,
    rol: str,
    token_version: int,
    expires_in: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)
    ttl = expires_in if expires_in is not None else timedelta(
        minutes=get_settings().access_token_ttl_minutes
    )
    payload: dict[str, Any] = {
        "sub": user_id,
        "buro_id": buro_id,
        "rol": rol,
        "tv": token_version,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """İmza, süre ve zorunlu alanları doğrular; geçersizse None döner."""
    if not token:
        return None
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=[_ALGORITHM],
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError:
        return None
