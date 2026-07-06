"""Form routes: upload a blank form, run the fill pipeline, review flagged fields, download.

Download is BLOCKED until every flagged field has been reviewed. No route ever submits
the form to an external portal.

Phase 2 implements upload + read-only draft retrieval only. Review/approve/correct,
download (gated), and rendering are Phase 3 — see SPEC-PHASE2.md.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi import Form as FormBody
from sqlalchemy.orm import Session

from app.agent.tools.form_schema_tool import known_types, load_template
from app.api.deps import get_current_user, get_db
from app.config import settings
from app.core.encryption import build_aad, decrypt_field
from app.core.logging import logger
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.user import User
from app.schemas.form import FormFieldOut, FormFieldSource, FormOut, FormUploadResponse
from app.services.storage import put_document
from app.workers.tasks import fill_form_task

router = APIRouter()

_READ_CHUNK_BYTES = 1024 * 1024


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


@router.post("/upload", response_model=FormUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_form(
    file: UploadFile = File(...),
    form_type: str = FormBody(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FormUploadResponse:
    if form_type not in known_types():
        raise _err(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown form type", "UNKNOWN_FORM_TYPE")

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

    try:
        display_name = load_template(form.declared_form_type).display_name
    except Exception:
        display_name = form.declared_form_type

    fields: list[FormFieldOut] = []
    if form.status == "filled":
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
        fill_error=form.fill_error,
        page_count=form.page_count,
        created_at=form.created_at,
        filled_at=form.filled_at,
        fields=fields,
    )


def _to_out(field: FormField, doc: Document | None) -> FormFieldOut:
    display_value = None
    if field.value_encrypted is not None:
        aad = build_aad(field.form_id, field.field_name)
        plaintext = decrypt_field(field.value_encrypted, aad=aad)
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
