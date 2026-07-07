"""Async, retryable jobs. Slow OCR/LLM work runs here so requests never block.

Jobs must be idempotent and must not silently drop — ocr_extract_task retries transient
failures with capped exponential backoff and always lands the document in a terminal
status (extracted/partial/failed/type_mismatch), never leaves it stuck "processing"
(SPEC-PHASE1.md Decision 8, §6.5).
    - ocr_extract_task:     ID document -> structured, encrypted profile fields
    - fill_form_task:       run the LangGraph pipeline for one form (Phase 2+)

Phase 3 (SPEC-PHASE3.md §6.5) extends fill_form_task: the profile snapshot now carries
decrypted source snippets and includes manual (document-less) candidates via an outer
join; a best-effort OpenCV skew check runs on the uploaded blank form; a `verifier`
closure is injected into the graph for the document_verification node's LLM-escalation
path; and the terminal status is `in_review` (any field outstanding) or `approved`
(none) instead of `filled`, which is retired.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent.graph import build_graph
from app.agent.tools.form_schema_tool import TemplateError
from app.agent.tools.profile_lookup_tool import CandidateView, ProfileSnapshot
from app.config import settings
from app.core.encryption import build_aad, decrypt_field, encrypt_field, mask_for
from app.core.logging import logger
from app.db.session import SessionLocal
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.profile import Profile, ProfileField
from app.services.extraction import GroundedField, extract_profile_fields
from app.services.image_quality import estimate_skew
from app.services.ocr.vision_llm import VisionExtractionError, classify_form, verify_value_on_document
from app.services.preprocessing import PreprocessingError, preprocess
from app.services.storage import get_document
from app.workers.celery_app import celery_app

_fill_graph = build_graph()


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


@celery_app.task(bind=True, max_retries=settings.fill_max_retries)
def fill_form_task(self, form_id: str) -> None:
    """Run the LangGraph agent pipeline to produce a draft + review queue for a form."""
    db = SessionLocal()
    try:
        _run_fill(self, db, form_id)
    finally:
        db.close()


def _run_fill(task, db: Session, form_id: str) -> None:
    form = db.get(Form, uuid.UUID(form_id))
    if form is None:
        logger.warning("fill_form_task form not found form_id=%s", form_id)
        return

    form.status = "processing"
    db.commit()

    try:
        raw_bytes = get_document(form.s3_key)
        images, page_count = preprocess(raw_bytes, form.content_type or "")
        form.page_count = page_count
        _apply_skew_check(form, images)
        snapshot = _build_profile_snapshot(db, form.user_id)
    except PreprocessingError as exc:
        # Malformed/undecodable upload — not retryable, the bytes won't change.
        _fail_form(form, db, str(exc))
        return
    except Exception:
        # Unclassified infra hiccup (S3 blip, transient network) — retryable rather
        # than failing a form over a blip (mirrors ocr_extract_task, Decision 8).
        _retry_or_fail_form(task, form, db, "transient storage/processing error")
        return

    verifier = _make_verifier(db)
    try:
        result = _fill_graph.invoke(
            {
                "user_id": str(form.user_id),
                "form_id": str(form.id),
                "declared_form_type": form.declared_form_type,
                "detected_form_type": None,
                "type_mismatch": False,
                "form_type": None,
                "field_specs": [],
                "fields": [],
            },
            config={
                "configurable": {
                    "snapshot": snapshot,
                    "images": images,
                    "classifier": classify_form,
                    "verifier": verifier,
                }
            },
        )
    except TemplateError:
        _fail_form(form, db, "unsupported form type")
        return
    except VisionExtractionError as exc:
        if exc.transient:
            _retry_or_fail_form(task, form, db, "vision-LLM call temporarily unavailable")
        else:
            _fail_form(form, db, "form classification or verification failed")
        return
    except Exception:
        _retry_or_fail_form(task, form, db, "transient fill error")
        return

    form.detected_form_type = result["detected_form_type"]

    if result["type_mismatch"]:
        form.status = "type_mismatch"
        form.fill_error = f"declared={form.declared_form_type} detected={result['detected_form_type']}"
        form.filled_at = _now()
        db.commit()
        logger.info("fill_form_task type_mismatch form_id=%s", form_id)
        return

    any_outstanding = _persist_form_fields(db, form, result["fields"])
    form.status = "in_review" if any_outstanding else "approved"
    form.filled_at = _now()
    db.commit()
    logger.info("fill_form_task done form_id=%s status=%s", form_id, form.status)


def _retry_or_fail_form(task, form: Form, db: Session, reason: str) -> None:
    db.rollback()
    countdown = settings.fill_retry_backoff_seconds * (2**task.request.retries)
    try:
        raise task.retry(countdown=countdown, exc=RuntimeError(reason))
    except MaxRetriesExceededError:
        _fail_form(form, db, "fill failed after retries")


def _fail_form(form: Form, db: Session, reason: str) -> None:
    form.status = "failed"
    form.fill_error = reason
    form.filled_at = _now()
    db.commit()
    logger.warning("fill_form_task failed form_id=%s reason=%s", form.id, reason)


def _apply_skew_check(form: Form, images: list[bytes]) -> None:
    """Best-effort input-quality guard (SPEC-PHASE3.md Decision 15/§8.4.4): a detector
    failure must never fail the fill over a quality heuristic, so any exception here
    is swallowed and simply leaves the warning unset."""
    if not images:
        return
    try:
        angle = estimate_skew(images[0])
    except Exception:
        logger.warning("fill_form_task skew_check_failed form_id=%s", form.id)
        return
    if abs(angle) > settings.skew_warn_degrees:
        form.skew_angle = angle
        form.placement_warning = (
            f"This scan looks rotated ~{abs(angle):.0f}°; coordinate-based field "
            "placement may be off. Re-scan or re-photograph the form upright for best "
            "results, or use a fillable PDF if one is available."
        )


def _make_verifier(db: Session) -> Callable[[str, str | None], bool]:
    """Builds the verifier closure the document_verification node escalates to on a
    deterministic miss: fetch that document's bytes (memoized per source_doc_id for
    the run), preprocess, call the vision-LLM (SPEC-PHASE3.md §6.5 step 4). A `None`
    source_doc_id (a manual, document-less candidate) can't be re-verified against a
    document — treat it as a fail if it ever reaches escalation."""
    cache: dict[str, list[bytes]] = {}

    def verifier(value: str, source_doc_id: str | None) -> bool:
        if source_doc_id is None:
            return False
        images = cache.get(source_doc_id)
        if images is None:
            doc = db.get(Document, uuid.UUID(source_doc_id))
            if doc is None:
                images = []
            else:
                raw_bytes = get_document(doc.s3_key)
                images, _ = preprocess(raw_bytes, doc.content_type or "")
            cache[source_doc_id] = images
        if not images:
            return False
        return verify_value_on_document(images, value)

    return verifier


def _build_profile_snapshot(db: Session, user_id: uuid.UUID) -> ProfileSnapshot:
    """Decrypts the user's profile into an in-memory snapshot for the graph to read.
    Plaintext lives only in-process for the duration of the fill (never persisted,
    never logged) — the graph's tools are pure and never touch the DB or crypto
    directly.

    Outer-joined against Document (Phase 3) so manual write-back candidates
    (nullable source_doc_id, SPEC-PHASE3.md §4.3) are included in the snapshot too."""
    profile = db.scalar(select(Profile).where(Profile.user_id == user_id))
    if profile is None:
        return {}

    rows = (
        db.query(ProfileField, Document)
        .outerjoin(Document, ProfileField.source_doc_id == Document.id)
        .filter(ProfileField.profile_id == profile.id)
        .all()
    )
    snapshot: ProfileSnapshot = {}
    for pf, doc in rows:
        aad = build_aad(pf.profile_id, pf.field_name)
        plaintext = decrypt_field(pf.effective_value_encrypted, aad=aad)
        snippet = (
            decrypt_field(pf.source_snippet_encrypted, aad=aad) if pf.source_snippet_encrypted else None
        )
        snapshot.setdefault(pf.field_name, []).append(
            CandidateView(
                profile_field_id=str(pf.id),
                source_doc_id=str(pf.source_doc_id) if pf.source_doc_id else None,
                doc_type=doc.declared_doc_type if doc is not None else "manual",
                value=plaintext,
                confidence=pf.confidence,
                status=pf.status,
                created_at=pf.created_at,
                source_snippet=snippet,
            )
        )
    return snapshot


def _persist_form_fields(db: Session, form: Form, fields: list[dict]) -> bool:
    """Idempotent re-run: wipes this form's prior fields before re-writing. Returns
    whether any field is outstanding (needs_review) — the caller derives the form's
    terminal status from this (SPEC-PHASE3.md Decision 8)."""
    db.execute(delete(FormField).where(FormField.form_id == form.id))
    any_outstanding = False
    for f in fields:
        value_encrypted = None
        value_masked = None
        if f["value"] is not None:
            aad = build_aad(form.id, f["field_name"])
            value_encrypted = encrypt_field(f["value"], aad=aad)
            if f["profile_key"]:
                value_masked = mask_for(f["profile_key"], f["value"])

        if f["needs_review"]:
            any_outstanding = True

        db.add(
            FormField(
                form_id=form.id,
                field_name=f["field_name"],
                profile_key=f["profile_key"],
                value_encrypted=value_encrypted,
                value_masked=value_masked,
                profile_field_id=uuid.UUID(f["profile_field_id"]) if f["profile_field_id"] else None,
                source_doc_id=uuid.UUID(f["source_doc_id"]) if f["source_doc_id"] else None,
                confidence=f["confidence"],
                confidence_band=f["confidence_band"],
                high_stakes=f["high_stakes"],
                transformed=f["transformed"],
                verified=f["verified"],
                verification_method=f["verification_method"],
                needs_review=f["needs_review"],
                review_reason=f["review_reason"],
                reviewed=False,
                review_action=None,
                reviewed_at=None,
                corrected_value_encrypted=None,
                flags=f["flags"],
            )
        )
    db.commit()
    return any_outstanding
