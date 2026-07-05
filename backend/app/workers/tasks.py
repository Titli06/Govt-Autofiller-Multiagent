"""Async, retryable jobs. Slow OCR/LLM work runs here so requests never block.

Jobs must be idempotent and must not silently drop — ocr_extract_task retries transient
failures with capped exponential backoff and always lands the document in a terminal
status (extracted/partial/failed/type_mismatch), never leaves it stuck "processing"
(SPEC-PHASE1.md Decision 8, §6.5).
    - ocr_extract_task:     ID document -> structured, encrypted profile fields
    - fill_form_task:       run the LangGraph pipeline for one form (Phase 2+)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.encryption import build_aad, encrypt_field, mask_for
from app.core.logging import logger
from app.db.session import SessionLocal
from app.models.document import Document
from app.models.profile import Profile, ProfileField
from app.services.extraction import GroundedField, extract_profile_fields
from app.services.ocr.vision_llm import VisionExtractionError
from app.services.preprocessing import PreprocessingError, preprocess
from app.services.storage import get_document
from app.workers.celery_app import celery_app


def _now() -> datetime:
    return datetime.now(timezone.utc)


@celery_app.task(bind=True, max_retries=settings.ocr_max_retries)
def ocr_extract_task(self, document_id: str) -> None:
    """Extract structured profile data from an uploaded ID document."""
    db = SessionLocal()
    try:
        _run(self, db, document_id)
    finally:
        db.close()


def _run(task, db: Session, document_id: str) -> None:
    doc = db.get(Document, uuid.UUID(document_id))
    if doc is None:
        logger.warning("ocr_extract_task document not found document_id=%s", document_id)
        return

    doc.ocr_status = "processing"
    db.commit()

    try:
        raw_bytes = get_document(doc.s3_key)
        images, page_count = preprocess(raw_bytes, doc.content_type or "")
        doc.page_count = page_count
        result = extract_profile_fields(images, doc.declared_doc_type)
    except VisionExtractionError as exc:
        if exc.transient:
            _retry_or_fail(task, doc, db, "vision extraction temporarily unavailable")
        else:
            _fail(doc, db, "extraction failed")
        return
    except PreprocessingError as exc:
        # Malformed/undecodable upload — not retryable, the bytes won't change.
        _fail(doc, db, str(exc))
        return
    except Exception:
        # Unclassified infra hiccup (S3 blip, transient network) — treat as retryable
        # rather than failing a document over a blip (Decision 8).
        _retry_or_fail(task, doc, db, "transient storage/processing error")
        return

    if result.type_mismatch:
        doc.ocr_status = "type_mismatch"
        doc.detected_doc_type = result.detected_doc_type
        doc.ocr_error = f"declared={doc.declared_doc_type} detected={result.detected_doc_type}"
        doc.extracted_at = _now()
        db.commit()
        logger.info("ocr_extract_task type_mismatch document_id=%s", document_id)
        return

    doc.detected_doc_type = result.detected_doc_type
    profile = _get_or_create_profile(db, doc.user_id)
    _persist_candidates(db, profile, doc, result.fields)

    if result.fields and not result.missing_fields:
        doc.ocr_status = "extracted"
    elif result.fields:
        doc.ocr_status = "partial"
    else:
        doc.ocr_status = "failed"
        doc.ocr_error = "no fields could be extracted"

    doc.extracted_at = _now()
    db.commit()
    logger.info("ocr_extract_task done document_id=%s status=%s", document_id, doc.ocr_status)


def _retry_or_fail(task, doc: Document, db: Session, reason: str) -> None:
    db.rollback()
    countdown = settings.ocr_retry_backoff_seconds * (2**task.request.retries)
    try:
        raise task.retry(countdown=countdown, exc=RuntimeError(reason))
    except MaxRetriesExceededError:
        _fail(doc, db, "extraction failed after retries")


def _fail(doc: Document, db: Session, reason: str) -> None:
    doc.ocr_status = "failed"
    doc.ocr_error = reason
    doc.extracted_at = _now()
    db.commit()
    logger.warning("ocr_extract_task failed document_id=%s reason=%s", doc.id, reason)


def _get_or_create_profile(db: Session, user_id: uuid.UUID) -> Profile:
    profile = db.scalar(select(Profile).where(Profile.user_id == user_id))
    if profile is None:
        profile = Profile(user_id=user_id)
        db.add(profile)
        db.flush()
    return profile


def _persist_candidates(
    db: Session, profile: Profile, doc: Document, fields: list[GroundedField]
) -> None:
    # Idempotent re-run: wipe this document's prior candidates before re-writing (a
    # re-triggered task must not accumulate duplicate rows).
    db.execute(
        delete(ProfileField).where(
            ProfileField.profile_id == profile.id, ProfileField.source_doc_id == doc.id
        )
    )
    for grounded in fields:
        aad = build_aad(profile.id, grounded.field_name)
        value_encrypted = encrypt_field(grounded.value, aad=aad)
        snippet_encrypted = (
            encrypt_field(grounded.source_snippet, aad=aad) if grounded.source_snippet else None
        )

        if not grounded.format_valid:
            status = "failed_validation"
        elif grounded.high_stakes or grounded.confidence < settings.confidence_threshold:
            status = "needs_confirmation"
        else:
            status = "confirmed"

        db.add(
            ProfileField(
                profile_id=profile.id,
                source_doc_id=doc.id,
                field_name=grounded.field_name,
                value_encrypted=value_encrypted,
                value_masked=mask_for(grounded.field_name, grounded.value),
                source_snippet_encrypted=snippet_encrypted,
                confidence=grounded.confidence,
                confidence_band=grounded.confidence_band,
                high_stakes=grounded.high_stakes,
                status=status,
                validators=grounded.validators,
            )
        )
    db.commit()


@celery_app.task(bind=True, max_retries=3)
def fill_form_task(self, form_id: str) -> None:
    """Run the agent pipeline to produce a draft + review queue for a form."""
    # TODO: Phase 2
