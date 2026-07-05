"""Primary OCR/vision extraction via a vision-LLM (Anthropic Claude).

Robust to handwriting, skew, rotation, and mixed Hindi/English text. Used for all
messy real-world ID scans; the Tesseract path is only a cost-saver for clean docs.
"""

# TODO: extract(image_or_pdf, target_schema) -> structured JSON
