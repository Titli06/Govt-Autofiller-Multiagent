"""Field-level PII encryption/decryption (encrypt profile values at rest).

All profile field values are encrypted with PII_ENCRYPTION_KEY before persisting
and decrypted only when needed. Raw PII must never be logged.
"""

# TODO: encrypt_field(plaintext) -> bytes, decrypt_field(ciphertext) -> str
