"""Field-level PII encryption/decryption (encrypt profile values at rest).

AES-256-GCM, random 12-byte nonce per call, with a version byte prefix so a future key
rotation (adding version 2) is a non-breaking schema change. GCM is authenticated, so
tampering with stored ciphertext is detected rather than silently decrypting garbage.
Callers should pass an AAD binding the ciphertext to its row (see build_aad) so a value
copied to another (profile, field) pair fails to decrypt instead of silently "working".

The key is loaded from PII_ENCRYPTION_KEY lazily (not at import) so importing this module
never fails; the first real encrypt/decrypt call raises a clear EncryptionError if the key
is missing or malformed, rather than a 500 deep in a Celery task.
"""

from __future__ import annotations

import base64
import binascii
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_NONCE_SIZE = 12
_CURRENT_VERSION = 1


class EncryptionError(Exception):
    """Raised when the encryption key is missing/malformed, or decryption fails
    (tampered ciphertext, wrong AAD, or an unsupported key version)."""


def _load_key() -> bytes:
    try:
        key = base64.b64decode(settings.pii_encryption_key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EncryptionError("PII_ENCRYPTION_KEY must be valid base64") from exc
    if len(key) != 32:
        raise EncryptionError("PII_ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256)")
    return key


def build_aad(profile_id: uuid.UUID | str, field_name: str) -> bytes:
    """AAD binding a ciphertext to the row it belongs to — see module docstring."""
    return f"{profile_id}:{field_name}".encode("utf-8")


def encrypt_field(plaintext: str, aad: bytes | None = None) -> bytes:
    key = _load_key()
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return bytes([_CURRENT_VERSION]) + nonce + ciphertext


def decrypt_field(blob: bytes, aad: bytes | None = None) -> str:
    if len(blob) < 1 + _NONCE_SIZE:
        raise EncryptionError("ciphertext too short")
    version = blob[0]
    if version != _CURRENT_VERSION:
        raise EncryptionError(f"unsupported key version {version}")
    key = _load_key()
    nonce = blob[1 : 1 + _NONCE_SIZE]
    ciphertext = blob[1 + _NONCE_SIZE :]
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise EncryptionError(
            "decryption failed: tampered ciphertext, wrong AAD, or wrong key"
        ) from exc
    return plaintext.decode("utf-8")


# --- Display masking for high-sensitivity fields (Aadhaar/PAN never shown in full) ----------


def mask_aadhaar(value: str) -> str:
    digits = value.replace(" ", "")
    return f"XXXX XXXX {digits[-4:]}"


def mask_pan(value: str) -> str:
    v = value.strip().upper()
    return f"XXXXXX{v[-4:]}"


_MASKERS = {"aadhaar_number": mask_aadhaar, "pan_number": mask_pan}


def mask_for(field_name: str, value: str) -> str | None:
    """Returns the masked display form for high-sensitivity fields, else None
    (meaning: this field is not subject to masking — display the plaintext)."""
    masker = _MASKERS.get(field_name)
    return masker(value) if masker else None
