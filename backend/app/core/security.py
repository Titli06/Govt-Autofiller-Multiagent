"""JWT issuance/verification, bcrypt password hashing, and opaque-token helpers.

Pure functions only — no DB access. Callers own persistence. Refresh and email-
verification tokens are random opaque strings; only their SHA-256 hash is ever stored
(see hash_token), so a database read cannot reconstruct a usable token.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import settings


class TokenError(Exception):
    """Raised when an access token is missing, malformed, expired, or the wrong type."""


# --- Passwords ---------------------------------------------------------------
# We use the bcrypt library directly (not passlib): passlib 1.7.4 is unmaintained and its
# bcrypt backend raises on bcrypt>=4.1. Password length is bounded to <=72 bytes at the
# schema layer (RegisterRequest); verify_password still guards defensively.


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash, or an over-long password on login — treat as non-match.
        return False


# A precomputed bcrypt hash of a random string. login() verifies against this when the
# email is unknown so the request does the same work either way (no timing enumeration).
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


# --- Access tokens (JWT) -----------------------------------------------------


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:  # covers bad signature and expiry
        raise TokenError(str(exc)) from exc
    if claims.get("type") != "access":
        raise TokenError("wrong token type")
    if not claims.get("sub"):
        raise TokenError("missing subject")
    return claims


# --- Opaque tokens (refresh + email verification) ----------------------------


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
