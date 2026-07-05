"""LLM-based structured field extraction under a strict JSON schema.

Turns OCR output into typed profile fields. Strict schema constraints bound the
model to prevent hallucinated fields. Always paired with confidence scoring +
verification downstream — never trusted blindly.
"""

# TODO: extract_profile_fields(ocr_result, schema) -> dict
