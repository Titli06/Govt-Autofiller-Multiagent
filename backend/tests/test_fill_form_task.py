"""fill_form_task's business logic (_run_fill), exercised directly against a fake
Celery task object — mirrors test_ocr_task.py's pattern. get_document, preprocess, and
classify_form are mocked; the LangGraph pipeline itself runs for real against a seeded
Profile/ProfileField snapshot (no DB/crypto inside the graph — see agent/graph.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from celery.exceptions import MaxRetriesExceededError

from app.core.encryption import build_aad, decrypt_field, encrypt_field
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.profile import Profile, ProfileField
from app.models.user import User
from app.services.ocr.vision_llm import VisionExtractionError
from app.services.preprocessing import PreprocessingError
from app.workers.tasks import _run_fill


class _FakeTask:
    """Stands in for Celery's bound `self` — enough surface for _retry_or_fail_form."""

    def __init__(self, retries=0, max_retries=3, exhausted=False):
        self.request = SimpleNamespace(retries=retries)
        self.max_retries = max_retries
        self.retry_calls: list[dict] = []
        self._exhausted = exhausted

    def retry(self, countdown=None, exc=None):
        self.retry_calls.append({"countdown": countdown, "exc": exc})
        if self._exhausted:
            raise MaxRetriesExceededError()
        raise RuntimeError("celery Retry (simulated) — task rescheduled")


def _make_user(db_session) -> User:
    user = User(email="u@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def _make_form(db_session, user, form_type="income_certificate") -> Form:
    form = Form(
        user_id=user.id,
        declared_form_type=form_type,
        s3_key="forms/x/y.jpg",
        content_type="image/jpeg",
        status="pending",
    )
    db_session.add(form)
    db_session.commit()
    return form


def _seed_profile_field(
    db_session, user, field_name, value, *, confidence=0.95, status="confirmed"
) -> ProfileField:
    profile = db_session.query(Profile).filter_by(user_id=user.id).one_or_none()
    if profile is None:
        profile = Profile(user_id=user.id)
        db_session.add(profile)
        db_session.flush()

    doc = Document(
        user_id=user.id, declared_doc_type="aadhaar", s3_key="documents/x/y.jpg", ocr_status="extracted"
    )
    db_session.add(doc)
    db_session.flush()

    band = "high" if confidence >= 0.9 else "medium" if confidence >= 0.7 else "low"
    aad = build_aad(profile.id, field_name)
    pf = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc.id,
        field_name=field_name,
        value_encrypted=encrypt_field(value, aad=aad),
        confidence=confidence,
        confidence_band=band,
        high_stakes=field_name in {"aadhaar_number", "pan_number", "dob"},
        status=status,
    )
    db_session.add(pf)
    db_session.commit()
    return pf


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_successful_fill_creates_form_fields(mock_get_doc, mock_preprocess, mock_classify, db_session):
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "full_name", "Ravi Kumar")
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "filled"
    assert form.filled_at is not None
    assert form.detected_form_type == "income_certificate"

    rows = db_session.query(FormField).filter_by(form_id=form.id).all()
    assert len(rows) == 6
    by_name = {r.field_name: r for r in rows}
    aad = build_aad(form.id, "applicant_name")
    assert decrypt_field(by_name["applicant_name"].value_encrypted, aad=aad) == "Ravi Kumar"
    assert by_name["annual_income"].value_encrypted is None
    assert by_name["annual_income"].review_reason == "no_mapping"
    assert by_name["father_name"].review_reason == "no_candidate"


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_aadhaar_field_masked_and_flagged(mock_get_doc, mock_preprocess, mock_classify, db_session):
    user = _make_user(db_session)
    _seed_profile_field(
        db_session, user, "aadhaar_number", "234123412346", confidence=0.9, status="needs_confirmation"
    )
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    field = db_session.query(FormField).filter_by(form_id=form.id, field_name="aadhaar_number").one()
    assert field.value_masked == "XXXX XXXX 2346"
    assert field.needs_review is True
    assert field.flags["unverified_source"] is True


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_user_confirmed_high_stakes_field_gets_full_confidence_but_still_flagged(
    mock_get_doc, mock_preprocess, mock_classify, db_session
):
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "dob", "1998-04-12", confidence=0.6, status="user_confirmed")
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    field = db_session.query(FormField).filter_by(form_id=form.id, field_name="date_of_birth").one()
    aad = build_aad(form.id, "date_of_birth")
    assert decrypt_field(field.value_encrypted, aad=aad) == "12/04/1998"
    assert field.transformed is True
    assert field.confidence == 1.0
    assert field.needs_review is True
    assert field.review_reason == "high_stakes"


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_no_profile_all_fields_flagged_missing_but_form_still_filled(
    mock_get_doc, mock_preprocess, mock_classify, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    rows = db_session.query(FormField).filter_by(form_id=form.id).all()
    assert len(rows) == 6
    assert all(r.value_encrypted is None for r in rows)
    assert all(r.needs_review for r in rows)
    db_session.refresh(form)
    assert form.status == "filled"  # an all-flagged draft is still "filled", not "partial"


@patch("app.workers.tasks.classify_form", return_value="scholarship_application")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_type_mismatch_writes_no_fields(mock_get_doc, mock_preprocess, mock_classify, db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="income_certificate")

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "type_mismatch"
    assert form.detected_form_type == "scholarship_application"
    assert "declared=income_certificate" in form.fill_error
    assert "detected=scholarship_application" in form.fill_error
    assert db_session.query(FormField).filter_by(form_id=form.id).count() == 0


@patch("app.workers.tasks.classify_form", return_value="unknown")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_unknown_classification_still_fills_declared_type(
    mock_get_doc, mock_preprocess, mock_classify, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "filled"
    assert form.detected_form_type == "unknown"


@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_unknown_declared_form_type_fails_terminal(mock_get_doc, mock_preprocess, db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="passport_renewal")

    task = _FakeTask()
    _run_fill(task, db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "failed"
    assert form.fill_error == "unsupported form type"
    assert task.retry_calls == []


@patch("app.workers.tasks.preprocess")
@patch("app.workers.tasks.get_document", return_value=b"garbage")
def test_preprocessing_error_fails_form_no_retry(mock_get_doc, mock_preprocess, db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user)
    mock_preprocess.side_effect = PreprocessingError("could not decode document: garbage")

    task = _FakeTask()
    _run_fill(task, db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "failed"
    assert form.fill_error == "could not decode document: garbage"
    assert task.retry_calls == []


@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
@patch("app.workers.tasks.classify_form")
def test_transient_classification_error_retries(mock_classify, mock_get_doc, mock_preprocess, db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user)
    mock_classify.side_effect = VisionExtractionError("rate limited", transient=True)

    task = _FakeTask(retries=0, exhausted=False)
    with pytest.raises(RuntimeError, match="Retry"):
        _run_fill(task, db_session, str(form.id))

    assert len(task.retry_calls) == 1
    db_session.refresh(form)
    assert form.status == "processing"  # still mid-flight, not yet failed


@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
@patch("app.workers.tasks.classify_form")
def test_transient_classification_error_exhausted_retries_fails_form(
    mock_classify, mock_get_doc, mock_preprocess, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user)
    mock_classify.side_effect = VisionExtractionError("still rate limited", transient=True)

    task = _FakeTask(retries=3, exhausted=True)
    _run_fill(task, db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "failed"
    assert form.fill_error == "fill failed after retries"


@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
@patch("app.workers.tasks.classify_form")
def test_terminal_classification_error_fails_without_retry(
    mock_classify, mock_get_doc, mock_preprocess, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user)
    mock_classify.side_effect = VisionExtractionError("bad request", transient=False)

    task = _FakeTask()
    _run_fill(task, db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "failed"
    assert task.retry_calls == []


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_rerun_is_idempotent_no_duplicate_fields(mock_get_doc, mock_preprocess, mock_classify, db_session):
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "full_name", "Ravi Kumar")
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))
    _run_fill(_FakeTask(), db_session, str(form.id))  # re-trigger

    rows = db_session.query(FormField).filter_by(form_id=form.id, field_name="applicant_name").all()
    assert len(rows) == 1
