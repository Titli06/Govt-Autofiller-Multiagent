"""Async, retryable jobs. Slow OCR/LLM work runs here so requests never block.

Jobs must be idempotent and must not silently drop — configure retries with backoff.
    - ocr_extract_task:     ID document -> structured profile JSON
    - fill_form_task:       run the LangGraph pipeline for one form
    - verify_form_task:     re-verify a form's fields against source docs
"""

from app.workers.celery_app import celery_app


@celery_app.task(bind=True, max_retries=3)
def ocr_extract_task(self, document_id: str) -> None:
    """Extract structured profile data from an uploaded ID document."""
    # TODO


@celery_app.task(bind=True, max_retries=3)
def fill_form_task(self, form_id: str) -> None:
    """Run the agent pipeline to produce a draft + review queue for a form."""
    # TODO
