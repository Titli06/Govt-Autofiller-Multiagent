"""Fallback OCR (Tesseract) for clearly clean, typed documents — a cost-saver.

Do not use for handwriting, skewed scans, or varied ID layouts; route those to the
vision-LLM path instead.
"""

# TODO: extract_text(image) -> str ; is_clean_typed(image) -> bool
