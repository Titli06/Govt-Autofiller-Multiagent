"""Profile routes: view the verified data store; confirm/correct flagged fields.

DELETE / (cascade delete of profile + documents + history) is Phase 5 — first-class
data-minimization feature, not built yet; the FKs are already ON DELETE CASCADE.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.encryption import build_aad, decrypt_field, encrypt_field, mask_for
from app.core.validators import is_valid_aadhaar, is_valid_pan, normalize_gender, parse_dob
from app.models.document import Document
from app.models.profile import Profile, ProfileField
from app.models.user import User
from app.schemas.profile import CorrectFieldRequest, ProfileFieldOut, ProfileFieldSource, ProfileOut

router = APIRouter()

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


def _get_source_document(db: Session, field: ProfileField) -> Document:
    doc = db.get(Document, field.source_doc_id)
    # source_doc_id is ON DELETE CASCADE — the field row can't outlive its document.
    assert doc is not None
    return doc


def _to_out(field: ProfileField, doc: Document) -> ProfileFieldOut:
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
        source=ProfileFieldSource(document_id=doc.id, doc_type=doc.declared_doc_type),
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
