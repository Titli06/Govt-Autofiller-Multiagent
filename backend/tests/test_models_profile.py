"""DB-level checks for the Phase 1 models: multi-candidate storage, cascade deletes,
and the (profile_id, field_name, source_doc_id) uniqueness constraint (Decision 1)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.document import Document
from app.models.profile import Profile, ProfileField
from app.models.user import User


def _make_user(db_session) -> User:
    user = User(email="u@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def _make_document(db_session, user: User, doc_type: str = "aadhaar") -> Document:
    doc = Document(
        user_id=user.id,
        declared_doc_type=doc_type,
        s3_key=f"documents/{user.id}/{uuid.uuid4()}.jpg",
        ocr_status="pending",
    )
    db_session.add(doc)
    db_session.flush()
    return doc


def test_multi_candidate_same_field_different_docs(db_session):
    """Two documents contributing the same field_name produce two distinct rows."""
    user = _make_user(db_session)
    profile = Profile(user_id=user.id)
    db_session.add(profile)
    db_session.flush()

    doc_a = _make_document(db_session, user, "aadhaar")
    doc_b = _make_document(db_session, user, "pan")

    f1 = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc_a.id,
        field_name="full_name",
        value_encrypted=b"cipher-a",
        confidence=0.97,
        confidence_band="high",
        high_stakes=False,
        status="confirmed",
    )
    f2 = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc_b.id,
        field_name="full_name",
        value_encrypted=b"cipher-b",
        confidence=0.95,
        confidence_band="high",
        high_stakes=False,
        status="confirmed",
    )
    db_session.add_all([f1, f2])
    db_session.commit()

    rows = db_session.query(ProfileField).filter_by(profile_id=profile.id, field_name="full_name").all()
    assert len(rows) == 2
    assert {r.source_doc_id for r in rows} == {doc_a.id, doc_b.id}


def test_unique_constraint_blocks_duplicate_candidate(db_session):
    user = _make_user(db_session)
    profile = Profile(user_id=user.id)
    db_session.add(profile)
    db_session.flush()
    doc = _make_document(db_session, user)

    f1 = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc.id,
        field_name="dob",
        value_encrypted=b"cipher-1",
        confidence=0.9,
        confidence_band="high",
        high_stakes=True,
        status="needs_confirmation",
    )
    db_session.add(f1)
    db_session.commit()

    f2 = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc.id,
        field_name="dob",  # same (profile_id, field_name, source_doc_id)
        value_encrypted=b"cipher-2",
        confidence=0.5,
        confidence_band="low",
        high_stakes=True,
        status="needs_confirmation",
    )
    db_session.add(f2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_effective_value_prefers_correction(db_session):
    user = _make_user(db_session)
    profile = Profile(user_id=user.id)
    db_session.add(profile)
    db_session.flush()
    doc = _make_document(db_session, user)

    field = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc.id,
        field_name="full_name",
        value_encrypted=b"extracted",
        confidence=0.6,
        confidence_band="medium",
        high_stakes=False,
        status="needs_confirmation",
    )
    db_session.add(field)
    db_session.commit()

    assert field.effective_value_encrypted == b"extracted"

    field.corrected_value_encrypted = b"corrected"
    field.status = "user_corrected"
    db_session.commit()

    assert field.effective_value_encrypted == b"corrected"


def test_one_profile_per_user_unique(db_session):
    user = _make_user(db_session)
    db_session.add(Profile(user_id=user.id))
    db_session.commit()

    db_session.add(Profile(user_id=user.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --- Phase 3: manual write-back candidates (nullable source_doc_id, origin) ----------


def test_manual_candidate_allows_null_source_doc(db_session):
    user = _make_user(db_session)
    profile = Profile(user_id=user.id)
    db_session.add(profile)
    db_session.flush()

    manual = ProfileField(
        profile_id=profile.id,
        source_doc_id=None,
        origin="manual",
        field_name="father_name",
        value_encrypted=b"cipher",
        confidence=1.0,
        confidence_band="high",
        high_stakes=False,
        status="user_corrected",
    )
    db_session.add(manual)
    db_session.commit()

    db_session.refresh(manual)
    assert manual.source_doc_id is None
    assert manual.origin == "manual"


def test_origin_defaults_to_document(db_session):
    user = _make_user(db_session)
    profile = Profile(user_id=user.id)
    db_session.add(profile)
    db_session.flush()
    doc = _make_document(db_session, user)

    field = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc.id,
        field_name="full_name",
        value_encrypted=b"cipher",
        confidence=0.9,
        confidence_band="high",
        high_stakes=False,
        status="confirmed",
    )
    db_session.add(field)
    db_session.commit()

    db_session.refresh(field)
    assert field.origin == "document"


def test_multiple_manual_candidates_for_same_field_allowed(db_session):
    # NULL source_doc_id makes each row distinct under the unique constraint —
    # multiple hand-typed corrections for the same field_name don't collide.
    user = _make_user(db_session)
    profile = Profile(user_id=user.id)
    db_session.add(profile)
    db_session.flush()

    for i in range(2):
        db_session.add(
            ProfileField(
                profile_id=profile.id,
                source_doc_id=None,
                origin="manual",
                field_name="father_name",
                value_encrypted=f"cipher-{i}".encode(),
                confidence=1.0,
                confidence_band="high",
                high_stakes=False,
                status="user_corrected",
            )
        )
    db_session.commit()

    rows = db_session.query(ProfileField).filter_by(profile_id=profile.id, field_name="father_name").all()
    assert len(rows) == 2
