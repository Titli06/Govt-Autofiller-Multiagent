"""Unit tests for core/security: password hashing + access-token round-trips."""

from __future__ import annotations

import time

import pytest

from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong password", h)


def test_verify_password_handles_malformed_hash():
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_access_token_roundtrip():
    token = create_access_token("user-123")
    claims = decode_access_token(token)
    assert claims["sub"] == "user-123"
    assert claims["type"] == "access"
    assert "jti" in claims


def test_access_token_rejects_tampered():
    token = create_access_token("user-123")
    with pytest.raises(TokenError):
        decode_access_token(token + "tamper")


def test_access_token_rejects_expired(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "access_token_expire_minutes", -1)
    token = create_access_token("user-123")
    time.sleep(0.01)
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_decode_rejects_non_access_type():
    # A token that isn't of type "access" (e.g. hand-forged) must be rejected.
    from jose import jwt

    from app.config import settings

    forged = jwt.encode(
        {"sub": "x", "type": "refresh"}, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_hash_token_is_deterministic_and_opaque():
    raw = generate_opaque_token()
    assert hash_token(raw) == hash_token(raw)
    assert raw not in hash_token(raw)
    assert len(hash_token(raw)) == 64
