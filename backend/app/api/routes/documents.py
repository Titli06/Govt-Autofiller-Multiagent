"""Document routes: upload ID docs (drag-drop / camera), triggers async OCR extraction."""

from fastapi import APIRouter

router = APIRouter()

# TODO: POST /upload (enqueue ocr_extract_task), GET /{id}/status
