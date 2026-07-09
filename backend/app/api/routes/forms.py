"""Form routes: upload a blank form, run the fill pipeline, review flagged fields, download.

Download is BLOCKED until every flagged field has been reviewed. No route ever submits
the form to an external portal.

Phase 2 implemented upload + read-only draft retrieval only. Phase 3 (SPEC-PHASE3.md
§8) adds the review projection/action endpoints, the gated download, and the
side-by-side blank-form file endpoint.

Phase 4 (SPEC-PHASE4.md §8) relaxes the upload gate to accept any non-empty
form_type — an unknown one now triggers schema inference downstream rather than a
422. `_effective_form_type` resolves which form_type a TEMPLATE lookup (display name,
render) should use: normally the form's own declared_form_type, but when Decision 2's
confident-detection override fired (an unseen declared label the vision-LLM
confidently recognized as a known type), that's `detected_form_type`, not the raw
declared string — the declared string was never a real template and load_template
would raise for it.

Phase 6 (SPEC-PHASE6.md §6.3): `submit_review_action` calls
`metrics.instrumentation.record_review` whenever the form's status flips to
`approved` (including a re-approval after a Decision-9 reopen), recording review
latency + approved-as-is/corrected counts onto the form's `pipeline_run` row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi import Form as FormBody
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.form_schema_tool import TemplateError, known_types, load_template
from app.api.deps import get_current_user, get_db
from app.config import settings
from app.core.encryption import build_aad, decrypt_field, encrypt_field, mask_for
from app.core.logging import logger
from app.core.validators import (
    is_valid_aadhaar,
    is_valid_pan,
    normalize_aadhaar,
    normalize_dob,
    normalize_gender,
    normalize_pan,
    parse_dob,
)
from app.metrics.instrumentation import record_review
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.profile import Profile, ProfileField
from app.models.user import User
from app.schemas.form import (
    FormFieldOut,
    FormFieldReviewOut,
    FormFieldSource,
    FormOut,
    FormReviewOut,
    FormUploadResponse,
    ReviewActionRequest,
    ReviewActionResponse,
)
from app.services.form_renderer import RenderError, RenderField, render
from app.services.storage import get_document, put_document
from app.workers.tasks import fill_form_task

router = APIRouter()

_READ_CHUNK_BYTES = 1024 * 1024
_FIELDS_VISIBLE_STATUSES = {"in_review", "approved"}

# Re-validate a correction against the same format rule extraction grounding uses
# (mirrors app/api/routes/profile.py) — free-text form fields (name/address/income)
# have no format rule and are accepted verbatim (Decision 12).
_FIELD_VALIDATORS = {
    "aadhaar_number": is_valid_aadhaar,
    "pan_number": is_valid_pan,
    "dob": lambda v: parse_dob(v) is not None,
    "gender": lambda v: normalize_gender(v) is not None,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(status_code: int, detail: str, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"detail": detail, "code": code})


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Reads the upload in chunks, aborting as soon as it exceeds max_bytes rather than
    buffering an arbitrarily large file first."""
    data = bytearray()
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise _err(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large", "FILE_TOO_LARGE")
    return bytes(data)


def _get_owned_form(form_id: uuid.UUID, db: Session, user: User) -> Form:
    form = db.get(Form, form_id)
    if form is None or form.user_id != user.id:
        raise _err(status.HTTP_404_NOT_FOUND, "Form not found", "NOT_FOUND")
    return form


def _effective_form_type(form: Form) -> str:
    """The form_type a TEMPLATE lookup should use (SPEC-PHASE4.md Decision 2): the
    form's own declared_form_type, UNLESS a confident-detection override fired for an
    originally-unseen declared label — in which case the resolved type is the
    detected known type, not the (never-a-real-template) declared string."""
    if form.schema_source == "template" and form.detected_form_type in known_types():
        return form.detected_form_type
    return form.declared_form_type


def _display_name(form: Form) -> str:
    try:
        return load_template(_effective_form_type(form)).display_name
    except Exception:
        return form.declared_form_type


@router.post("/upload", response_model=FormUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_form(
    file: UploadFile = File(...),
    form_type: str = FormBody(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FormUploadResponse:
    form_type = form_type.strip()
    if not form_type:
        raise _err(status.HTTP_422_UNPROCESSABLE_ENTITY, "Form type is required", "MISSING_FORM_TYPE")
    form_type = form_type[:64]
    # No registry-membership check (SPEC-PHASE4.md Decision 4) — a type not in the
    # template registry now triggers schema inference downstream instead of a 422.

    content_type = file.content_type or ""
    if content_type not in settings.allowed_upload_content_types:
        raise _err(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported file type", "UNSUPPORTED_TYPE")

    data = await _read_capped(file, settings.max_upload_bytes)

    s3_key = put_document(str(user.id), data, content_type)
    form = Form(
        user_id=user.id,
        declared_form_type=form_type,
        s3_key=s3_key,
        content_type=content_type,
        byte_size=len(data),
        status="pending",
    )
    db.add(form)
    db.commit()
    db.refresh(form)

    try:
        fill_form_task.delay(str(form.id))
    except Exception:
        form.status = "failed"
        form.fill_error = "failed to enqueue fill job"
        db.commit()
        logger.error("form_upload enqueue_failed form_id=%s", form.id)
        raise _err(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Failed to enqueue fill job", "ENQUEUE_FAILED"
        )

    logger.info("form_upload form_id=%s user_id=%s", form.id, user.id)
    return FormUploadResponse(form_id=form.id, status=form.status)


@router.get("/{form_id}", response_model=FormOut)
def get_form(
    form_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FormOut:
    form = _get_owned_form(form_id, db, user)
    display_name = _display_name(form)

    fields: list[FormFieldOut] = []
    if form.status in _FIELDS_VISIBLE_STATUSES:
        rows = db.query(FormField).filter(FormField.form_id == form.id).all()
        doc_ids = {row.source_doc_id for row in rows if row.source_doc_id is not None}
        docs = {d.id: d for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
        fields = [
            _to_out(row, docs.get(row.source_doc_id) if row.source_doc_id is not None else None)
            for row in rows
        ]

    return FormOut(
        id=form.id,
        form_type=form.declared_form_type,
        display_name=display_name,
        detected_form_type=form.detected_form_type,
        status=form.status,
        schema_source=form.schema_source,
        fill_error=form.fill_error,
        page_count=form.page_count,
        created_at=form.created_at,
        filled_at=form.filled_at,
        fields=fields,
    )


def _to_out(field: FormField, doc: Document | None) -> FormFieldOut:
    display_value = None
    effective = field.effective_value_encrypted
    if effective is not None:
        aad = build_aad(field.form_id, field.field_name)
        plaintext = decrypt_field(effective, aad=aad)
        display_value = field.value_masked or plaintext
    return FormFieldOut(
        id=field.id,
        field_name=field.field_name,
        profile_key=field.profile_key,
        display_value=display_value,
        confidence=field.confidence,
        confidence_band=field.confidence_band,
        high_stakes=field.high_stakes,
        transformed=field.transformed,
        needs_review=field.needs_review,
        review_reason=field.review_reason,
        reviewed=field.reviewed,
        source=FormFieldSource(
            profile_field_id=field.profile_field_id,
            document_id=field.source_doc_id,
            doc_type=doc.declared_doc_type if doc else None,
        ),
    )


def _to_review_out(field: FormField, doc: Document | None) -> FormFieldReviewOut:
    display_value = None
    effective = field.effective_value_encrypted
    if effective is not None:
        aad = build_aad(field.form_id, field.field_name)
        plaintext = decrypt_field(effective, aad=aad)
        display_value = field.value_masked or plaintext
    return FormFieldReviewOut(
        id=field.id,
        field_name=field.field_name,
        profile_key=field.profile_key,
        display_value=display_value,
        confidence=field.confidence,
        confidence_band=field.confidence_band,
        verified=field.verified,
        verification_method=field.verification_method,
        high_stakes=field.high_stakes,
        transformed=field.transformed,
        needs_review=field.needs_review,
        review_reason=field.review_reason,
        reviewed=field.reviewed,
        review_action=field.review_action,
        outstanding=field.needs_review and not field.reviewed,
        source=FormFieldSource(
            profile_field_id=field.profile_field_id,
            document_id=field.source_doc_id,
            doc_type=doc.declared_doc_type if doc else None,
        ),
    )


def _docs_by_id(db: Session, rows: list[FormField]) -> dict[uuid.UUID, Document]:
    doc_ids = {row.source_doc_id for row in rows if row.source_doc_id is not None}
    if not doc_ids:
        return {}
    return {d.id: d for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()}


@router.get("/{form_id}/review", response_model=FormReviewOut)
def get_form_review(
    form_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FormReviewOut:
    form = _get_owned_form(form_id, db, user)
    display_name = _display_name(form)

    rows = db.query(FormField).filter(FormField.form_id == form.id).all()
    docs = _docs_by_id(db, rows)
    fields = [
        _to_review_out(row, docs.get(row.source_doc_id) if row.source_doc_id is not None else None)
        for row in rows
    ]
    outstanding = sum(1 for f in fields if f.outstanding)

    return FormReviewOut(
        id=form.id,
        form_type=form.declared_form_type,
        display_name=display_name,
        status=form.status,
        schema_source=form.schema_source,
        download_ready=form.status == "approved",
        total_fields=len(fields),
        outstanding_fields=outstanding,
        placement_warning=form.placement_warning,
        fields=fields,
    )


def _to_canonical(profile_key: str, value: str) -> tuple[str, bool]:
    """Converts a form-review correction (already format-checked against the FORM's
    expected format) into the canonical form the profile store uses (SPEC-PHASE3.md
    §8.2 Decision 10/11): dates re-parsed to ISO, IDs normalized. Free text (name/
    address/gender-with-no-match) passes through as-is."""
    if profile_key == "dob":
        iso = normalize_dob(value)
        return (iso or value, iso is not None)
    if profile_key == "aadhaar_number":
        return (normalize_aadhaar(value), is_valid_aadhaar(value))
    if profile_key == "pan_number":
        return (normalize_pan(value), is_valid_pan(value))
    if profile_key == "gender":
        canonical = normalize_gender(value)
        return (canonical or value, canonical is not None)
    return (value, True)


def _propagate_correction(
    db: Session, user: User, field: FormField, form_value: str
) -> str | None:
    """Decision 10/11: updates the source ProfileField candidate, or synthesizes a
    manual one for a corrected missing field. Returns a warning string when
    propagation is a no-op (a no_mapping field has no canonical target), else None."""
    if field.profile_key is None:
        return "This field has no matching profile field — the correction was saved to the form only."

    canonical, ok = _to_canonical(field.profile_key, form_value)
    if not ok:
        raise _err(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Value can't be converted to a saved profile value",
            "INVALID_VALUE",
        )

    if field.profile_field_id is not None:
        pf = db.get(ProfileField, field.profile_field_id)
        if pf is not None:
            aad = build_aad(pf.profile_id, pf.field_name)
            pf.corrected_value_encrypted = encrypt_field(canonical, aad=aad)
            pf.value_masked = mask_for(pf.field_name, canonical)
            pf.status = "user_corrected"
            pf.confidence = 1.0
            pf.confidence_band = "high"
            return None

    # No backing candidate (the field was missing): synthesize a manual one.
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is None:
        profile = Profile(user_id=user.id)
        db.add(profile)
        db.flush()

    aad = build_aad(profile.id, field.profile_key)
    db.add(
        ProfileField(
            profile_id=profile.id,
            source_doc_id=None,
            origin="manual",
            field_name=field.profile_key,
            value_encrypted=encrypt_field(canonical, aad=aad),
            value_masked=mask_for(field.profile_key, canonical),
            confidence=1.0,
            confidence_band="high",
            high_stakes=field.high_stakes,
            status="user_corrected",
        )
    )
    return None


@router.post("/{form_id}/review", response_model=ReviewActionResponse)
def submit_review_action(
    form_id: uuid.UUID,
    body: ReviewActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewActionResponse:
    form = _get_owned_form(form_id, db, user)
    field = (
        db.query(FormField)
        .filter(FormField.id == body.field_id, FormField.form_id == form.id)
        .one_or_none()
    )
    if field is None:
        raise _err(status.HTTP_404_NOT_FOUND, "Field not found", "NOT_FOUND")

    # Decision 9: a correction on an already-approved form deliberately re-opens the
    # field (below) rather than instantly re-resolving it.
    was_approved = form.status == "approved"
    warning: str | None = None

    if body.action == "approve":
        field.reviewed = True
        field.review_action = "approved"
        field.reviewed_at = _now()

    elif body.action == "approve_blank":
        if field.effective_value_encrypted is not None:
            raise _err(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Field already has a value", "NOT_BLANK"
            )
        field.reviewed = True
        field.review_action = "approved_blank"
        field.reviewed_at = _now()

    elif body.action == "correct":
        if body.value is None:
            raise _err(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "value is required for correct", "MISSING_VALUE"
            )
        value = body.value.strip()
        validator = _FIELD_VALIDATORS.get(field.profile_key or "")
        if validator is not None and not validator(value):
            raise _err(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Value fails validation for this field",
                "INVALID_VALUE",
            )

        aad = build_aad(form.id, field.field_name)
        field.corrected_value_encrypted = encrypt_field(value, aad=aad)
        field.value_masked = mask_for(field.profile_key, value) if field.profile_key else None
        field.verified = True
        field.verification_method = "user"
        field.confidence = 1.0
        field.confidence_band = "high"
        field.review_action = "corrected"
        field.reviewed_at = _now()
        field.reviewed = not was_approved

        if body.propagate_to_profile:
            warning = _propagate_correction(db, user, field, value)

    form.rendered_s3_key = None  # any successful review action invalidates the cache

    db.flush()
    rows = db.query(FormField).filter(FormField.form_id == form.id).all()
    outstanding = any(r.needs_review and not r.reviewed for r in rows)
    form.status = "in_review" if outstanding else "approved"
    if form.status == "approved":
        record_review(db, form)  # sets review latency + approved-as-is/corrected counts
    db.commit()

    doc = db.get(Document, field.source_doc_id) if field.source_doc_id is not None else None
    return ReviewActionResponse(
        field=_to_review_out(field, doc),
        status=form.status,
        download_ready=form.status == "approved",
        warning=warning,
    )


@router.get("/{form_id}/download")
def download_form(
    form_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    form = _get_owned_form(form_id, db, user)
    if form.status != "approved":
        raise _err(status.HTTP_409_CONFLICT, "Review is not complete", "REVIEW_INCOMPLETE")

    if form.rendered_s3_key is None:
        rows = db.query(FormField).filter(FormField.form_id == form.id).all()
        render_fields: list[RenderField] = []
        for row in rows:
            effective = row.effective_value_encrypted
            value = None
            if effective is not None:
                aad = build_aad(form.id, row.field_name)
                value = decrypt_field(effective, aad=aad)
            render_fields.append(RenderField(field_name=row.field_name, value=value, placement=row.placement))

        blank_bytes = get_document(form.s3_key)
        try:
            pdf_bytes = render(
                _effective_form_type(form),
                render_fields,
                blank_bytes,
                form.content_type or "application/pdf",
                schema_source=form.schema_source,
            )
        except (RenderError, TemplateError):
            logger.error("form_download render_failed form_id=%s", form.id)
            raise _err(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not render the form", "RENDER_FAILED"
            )

        form.rendered_s3_key = put_document(str(user.id), pdf_bytes, "application/pdf")
        db.commit()
    else:
        pdf_bytes = get_document(form.rendered_s3_key)

    logger.info("form_download form_id=%s", form.id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{form.declared_form_type}.pdf"'},
    )


@router.get("/{form_id}/file")
def get_form_file(
    form_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Serves the ORIGINAL blank uploaded form for the review page's side-by-side
    preview (SPEC-PHASE3.md §8.6) — mirrors GET /documents/{id}/file."""
    form = _get_owned_form(form_id, db, user)
    data = get_document(form.s3_key)
    return Response(content=data, media_type=form.content_type or "application/octet-stream")
