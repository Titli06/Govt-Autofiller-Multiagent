"""Profile routes: view the verified data store; confirm/correct flagged fields;
irreversibly purge all data (Phase 5, FR10, SPEC-PHASE5.md §6.2).

DELETE / is a **data-only** purge — profile, all profile fields, all documents, all
forms/form-fields, and every associated S3 object. The User row/session/refresh token
are untouched (SPEC-PHASE5.md Decision 1); the user stays logged in on an empty
dashboard afterward.

Phase 6 (SPEC-PHASE6.md §6.8) extends the purge to also delete the user's
`pipeline_run` metrics rows — they're user data, not an exempt audit trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.core.encryption import build_aad, decrypt_field, encrypt_field, mask_for
from app.core.logging import logger
from app.core.security import verify_password
from app.core.validators import is_valid_aadhaar, is_valid_pan, normalize_gender, parse_dob
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.metrics import PipelineRun
from app.models.profile import Profile, ProfileField
from app.models.user import User
from app.schemas.profile import (
    CorrectFieldRequest,
    DeleteProfileRequest,
    DeleteProfileResponse,
    ProfileFieldOut,
    ProfileFieldSource,
    ProfileOut,
)
from app.services.storage import delete_document

router = APIRouter()

_BUSY_STATUSES = ("pending", "processing")


def _aware(dt: datetime) -> datetime:
    """Normalize a possibly-naive DB datetime (SQLite) to tz-aware UTC for comparison
    (mirrors api/routes/auth.py's helper — SQLite doesn't round-trip tzinfo)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# Re-validate a correction against the same format rule the extraction grounding uses
# (SPEC-PHASE1.md §3.3) — free-text fields (name/address) have no format rule.
_FIELD_VALIDATORS = {
    "aadhaar_number": is_valid_aadhaar,
    "pan_number": is_valid_pan,
    "dob": lambda v: parse_dob(v) is not None,
    "gender": lambda v: normalize_gender(v) is not None,
}


def _err(status_code: int, detail: str, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"detail": detail, "code": code})


def _get_owned_field(field_id: uuid.UUID, db: Session, user: User) -> ProfileField:
    field = db.get(ProfileField, field_id)
    profile = db.get(Profile, field.profile_id) if field is not None else None
    if field is None or profile is None or profile.user_id != user.id:
        raise _err(status.HTTP_404_NOT_FOUND, "Field not found", "NOT_FOUND")
    return field


def _get_source_document(db: Session, field: ProfileField) -> Document | None:
    """None for a manual candidate (Phase 3 Decision 11: origin="manual",
    source_doc_id=NULL — a hand-typed form-review correction has no source document).
    Otherwise source_doc_id is ON DELETE CASCADE, so the field row can't outlive it."""
    if field.source_doc_id is None:
        return None
    doc = db.get(Document, field.source_doc_id)
    assert doc is not None
    return doc


def _to_out(field: ProfileField, doc: Document | None) -> ProfileFieldOut:
    aad = build_aad(field.profile_id, field.field_name)
    plaintext = decrypt_field(field.effective_value_encrypted, aad=aad)
    display_value = field.value_masked or plaintext
    return ProfileFieldOut(
        id=field.id,
        field_name=field.field_name,
        display_value=display_value,
        confidence=field.confidence,
        confidence_band=field.confidence_band,
        high_stakes=field.high_stakes,
        status=field.status,
        source=ProfileFieldSource(
            document_id=doc.id if doc else None,
            doc_type=doc.declared_doc_type if doc else None,
        ),
    )


@router.get("", response_model=ProfileOut)
def get_profile(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> ProfileOut:
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is None:
        return ProfileOut(fields=[])

    fields = db.query(ProfileField).filter(ProfileField.profile_id == profile.id).all()
    out = [_to_out(field, _get_source_document(db, field)) for field in fields]
    return ProfileOut(fields=out)


@router.post("/fields/{field_id}/confirm", response_model=ProfileFieldOut)
def confirm_field(
    field_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProfileFieldOut:
    field = _get_owned_field(field_id, db, user)
    field.status = "user_confirmed"
    db.commit()
    return _to_out(field, _get_source_document(db, field))


@router.post("/fields/{field_id}/correct", response_model=ProfileFieldOut)
def correct_field(
    field_id: uuid.UUID,
    body: CorrectFieldRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProfileFieldOut:
    field = _get_owned_field(field_id, db, user)

    value = body.value.strip()
    validator = _FIELD_VALIDATORS.get(field.field_name)
    if validator is not None and not validator(value):
        raise _err(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Value fails validation for this field",
            "INVALID_VALUE",
        )

    aad = build_aad(field.profile_id, field.field_name)
    field.corrected_value_encrypted = encrypt_field(value, aad=aad)
    field.value_masked = mask_for(field.field_name, value)
    field.status = "user_corrected"
    field.confidence = 1.0
    field.confidence_band = "high"
    db.commit()

    return _to_out(field, _get_source_document(db, field))


@router.delete("", response_model=DeleteProfileResponse)
def delete_my_data(
    body: DeleteProfileRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeleteProfileResponse:
    """Irreversible data-only purge (Decision 1): profile + all profile fields, all
    documents, all forms + form fields, and every associated S3 object. The User
    row/session/refresh token are untouched — the caller stays logged in."""
    if not verify_password(body.password, user.password_hash):
        raise _err(status.HTTP_403_FORBIDDEN, "Password is incorrect", "INVALID_PASSWORD")

    # In-flight guard with a staleness cutoff (Decisions 4/8): a job stuck longer than
    # this is treated as dead and no longer blocks, so a crashed worker can't
    # permanently wedge deletion. Filtered by status in SQL, but staleness is checked
    # in Python (via _aware) rather than a SQL datetime comparison — SQLite (tests)
    # doesn't round-trip tzinfo, so comparing naive vs. aware datetimes in a WHERE
    # clause is unreliable (mirrors api/routes/auth.py's refresh-token expiry checks).
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.purge_stale_job_seconds)
    busy_timestamps = [
        *db.scalars(
            select(Document.updated_at).where(
                Document.user_id == user.id, Document.ocr_status.in_(_BUSY_STATUSES)
            )
        ),
        *db.scalars(
            select(Form.updated_at).where(
                Form.user_id == user.id, Form.status.in_(_BUSY_STATUSES)
            )
        ),
    ]
    if any(_aware(dt) >= cutoff for dt in busy_timestamps):
        raise _err(
            status.HTTP_409_CONFLICT,
            "A document or form is still being processed; try again shortly",
            "JOBS_IN_PROGRESS",
        )

    # Gather every S3 key BEFORE any delete (Decision 7).
    doc_keys = list(db.scalars(select(Document.s3_key).where(Document.user_id == user.id)))
    form_key_rows = db.execute(
        select(Form.s3_key, Form.rendered_s3_key).where(Form.user_id == user.id)
    ).all()
    form_keys = [key for row in form_key_rows for key in row if key]

    # Best-effort S3 delete — never fatal, never logs a key/label/value (Decision 7).
    s3_deleted = 0
    s3_failed = 0
    for key in (*doc_keys, *form_keys):
        try:
            delete_document(key)
            s3_deleted += 1
        except Exception:
            s3_failed += 1
            logger.warning("profile_purge s3_delete_failed user_id=%s", user.id)

    # Single DB transaction. Children are deleted explicitly rather than relying
    # solely on the DB-level ON DELETE CASCADE/SET NULL FKs — those remain a correct
    # backstop in Postgres, but SQLite (used in tests) doesn't enforce FK actions
    # unless a pragma is set, and explicit deletes keep behavior identical either way.
    # Phase 6: pipeline_run rows are user data too (metrics are not an exempt audit
    # trail, SPEC-PHASE6.md Decision 6) — deleted by user_id like everything else, so
    # a full purge never orphans a metrics row.
    db.execute(delete(PipelineRun).where(PipelineRun.user_id == user.id))

    form_ids = select(Form.id).where(Form.user_id == user.id)
    db.execute(delete(FormField).where(FormField.form_id.in_(form_ids)))
    n_forms = db.execute(delete(Form).where(Form.user_id == user.id)).rowcount

    profile_ids = select(Profile.id).where(Profile.user_id == user.id)
    n_profile_fields = db.execute(
        delete(ProfileField).where(ProfileField.profile_id.in_(profile_ids))
    ).rowcount
    db.execute(delete(Profile).where(Profile.user_id == user.id))

    n_docs = db.execute(delete(Document).where(Document.user_id == user.id)).rowcount
    db.commit()

    logger.info(
        "profile_purge user_id=%s forms=%d documents=%d profile_fields=%d "
        "s3_deleted=%d s3_failed=%d",
        user.id,
        n_forms,
        n_docs,
        n_profile_fields,
        s3_deleted,
        s3_failed,
    )

    return DeleteProfileResponse(
        documents_deleted=n_docs,
        forms_deleted=n_forms,
        profile_fields_deleted=n_profile_fields,
        s3_objects_deleted=s3_deleted,
        s3_delete_failures=s3_failed,
    )
