"""Field-level PII encryption: round-trip, tamper detection, AAD binding, key validation."""

from __future__ import annotations

import base64

import pytest

from app.core.encryption import (
    EncryptionError,
    build_aad,
    decrypt_field,
    encrypt_field,
    mask_aadhaar,
    mask_for,
    mask_pan,
)


def test_round_trip():
    blob = encrypt_field("Rajesh Kumar")
    assert decrypt_field(blob) == "Rajesh Kumar"


def test_ciphertext_is_not_plaintext():
    blob = encrypt_field("9871234512")
    assert b"9871234512" not in blob


def test_same_plaintext_different_ciphertext_each_call():
    # Random nonce per call -> not ciphertext-searchable, which is fine (we look up by
    # plaintext field_name, not by encrypted value).
    a = encrypt_field("Rajesh Kumar")
    b = encrypt_field("Rajesh Kumar")
    assert a != b
    assert decrypt_field(a) == decrypt_field(b) == "Rajesh Kumar"


def test_round_trip_with_aad():
    aad = build_aad("profile-123", "full_name")
    blob = encrypt_field("Rajesh Kumar", aad=aad)
    assert decrypt_field(blob, aad=aad) == "Rajesh Kumar"


def test_wrong_aad_fails():
    aad = build_aad("profile-123", "full_name")
    wrong_aad = build_aad("profile-123", "father_name")
    blob = encrypt_field("Rajesh Kumar", aad=aad)
    with pytest.raises(EncryptionError):
        decrypt_field(blob, aad=wrong_aad)


def test_tampered_ciphertext_fails():
    blob = bytearray(encrypt_field("Rajesh Kumar"))
    blob[-1] ^= 0xFF  # flip a bit in the GCM tag/ciphertext
    with pytest.raises(EncryptionError):
        decrypt_field(bytes(blob))


def test_truncated_ciphertext_fails():
    with pytest.raises(EncryptionError):
        decrypt_field(b"\x01\x00\x00")


def test_unsupported_version_fails():
    blob = encrypt_field("Rajesh Kumar")
    tampered = bytes([99]) + blob[1:]
    with pytest.raises(EncryptionError):
        decrypt_field(tampered)


def test_bad_key_length_raises_clear_error(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "pii_encryption_key", base64.b64encode(b"short").decode())
    with pytest.raises(EncryptionError, match="32 bytes"):
        encrypt_field("value")


def test_non_base64_key_raises_clear_error(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "pii_encryption_key", "not-valid-base64!!!")
    with pytest.raises(EncryptionError, match="base64"):
        encrypt_field("value")


# --- Masking -------------------------------------------------------------------------


def test_mask_aadhaar():
    assert mask_aadhaar("987123451234") == "XXXX XXXX 1234"
    assert mask_aadhaar("9871 2345 1234") == "XXXX XXXX 1234"


def test_mask_pan():
    assert mask_pan("ABCDE1234F") == "XXXXXX234F"
    assert mask_pan("abcde1234f") == "XXXXXX234F"


def test_mask_for_dispatches_by_field_name():
    assert mask_for("aadhaar_number", "987123451234") == "XXXX XXXX 1234"
    assert mask_for("pan_number", "ABCDE1234F") == "XXXXXX234F"
    assert mask_for("full_name", "Rajesh Kumar") is None
