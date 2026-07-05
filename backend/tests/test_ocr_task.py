"""ocr_extract_task's business logic (_run), exercised directly against a fake Celery
task object so retry/backoff behavior is testable without a real broker. get_document,
preprocess, and extract_profile_fields are mocked — no real S3/vision calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from celery.exceptions import MaxRetriesExceededError

from app.core.encryption import decrypt_field
from app.models.document import Document
from app.models.profile import Profile, ProfileField
from app.models.user import User
from app.services.extraction import ExtractionResult, GroundedField
from app.services.ocr.vision_llm import VisionExtractionError
from app.services.preprocessing import PreprocessingError
from app.workers.tasks import _run


class _FakeTask:
    """Stands in for Celery's bound `self` — enough surface for _retry_or_fail."""

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


def _make_user_and_doc(db_session, doc_type="aadhaar", content_type="image/jpeg") -> Document:
    user = User(email="u@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    doc = Document(
        user_id=user.id,
        declared_doc_type=doc_type,
        s3_key="documents/x/y.jpg",
        content_type=content_type,
        ocr_status="pending",
    )
    db_session.add(doc)
    db_session.commit()
    return doc


def _grounded(field_name, value, snippet, confidence, band, high_stakes, format_valid=True):
    return GroundedField(
        field_name=field_name,
        value=value,
        source_snippet=snippet,
        confidence=confidence,
        confidence_band=band,
        high_stakes=high_stakes,
        format_valid=format_valid,
        validators={"snippet_contains": True, "format_valid": format_valid, "normalized": False},
    )


@patch("app.workers.tasks.extract_profile_fields")
@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_successful_extraction_creates_profile_and_fields(
    mock_get_doc, mock_preprocess, mock_extract, db_session
):
    doc = _make_user_and_doc(db_session)
    mock_extract.return_value = ExtractionResult(
        type_mismatch=False,
        detected_doc_type="aadhaar",
        fields=[
            _grounded("full_name", "Rajesh Kumar", "Name: Rajesh Kumar", 0.85, "medium", False),
            _grounded("aadhaar_number", "234123412346", "Aadhaar: 234123412346", 0.96, "high", True),
        ],
        missing_fields=[],
    )

    _run(_FakeTask(), db_session, str(doc.id))

    db_session.refresh(doc)
    assert doc.ocr_status == "extracted"
    assert doc.extracted_at is not None

    profile = db_session.query(Profile).filter_by(user_id=doc.user_id).one()
    fields = db_session.query(ProfileField).filter_by(profile_id=profile.id).all()
    assert len(fields) == 2
    by_name = {f.field_name: f for f in fields}
    assert decrypt_field(by_name["full_name"].value_encrypted, aad=f"{profile.id}:full_name".encode()) == "Rajesh Kumar"
    assert by_name["aadhaar_number"].value_masked == "XXXX XXXX 2346"
    assert by_name["full_name"].value_masked is None


@patch("app.workers.tasks.extract_profile_fields")
@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_partial_extraction_status(mock_get_doc, mock_preprocess, mock_extract, db_session):
    doc = _make_user_and_doc(db_session)
    mock_extract.return_value = ExtractionResult(
        type_mismatch=False,
        detected_doc_type="aadhaar",
        fields=[_grounded("full_name", "Rajesh Kumar", "Name: Rajesh Kumar", 0.85, "medium", False)],
        missing_fields=["aadhaar_number", "dob", "gender", "address"],
    )

    _run(_FakeTask(), db_session, str(doc.id))

    db_session.refresh(doc)
    assert doc.ocr_status == "partial"


@patch("app.workers.tasks.extract_profile_fields")
@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_type_mismatch_writes_no_fields(mock_get_doc, mock_preprocess, mock_extract, db_session):
    doc = _make_user_and_doc(db_session, doc_type="aadhaar")
    mock_extract.return_value = ExtractionResult(type_mismatch=True, detected_doc_type="pan")

    _run(_FakeTask(), db_session, str(doc.id))

    db_session.refresh(doc)
    assert doc.ocr_status == "type_mismatch"
    assert doc.detected_doc_type == "pan"
    assert "declared=aadhaar" in doc.ocr_error
    assert "detected=pan" in doc.ocr_error
    assert db_session.query(ProfileField).count() == 0
    assert db_session.query(Profile).count() == 0


@patch("app.workers.tasks.preprocess")
@patch("app.workers.tasks.get_document", return_value=b"garbage")
def test_preprocessing_error_fails_document_no_retry(mock_get_doc, mock_preprocess, db_session):
    doc = _make_user_and_doc(db_session)
    mock_preprocess.side_effect = PreprocessingError("could not decode document: garbage")

    task = _FakeTask()
    _run(task, db_session, str(doc.id))

    db_session.refresh(doc)
    assert doc.ocr_status == "failed"
    assert doc.ocr_error == "could not decode document: garbage"
    assert task.retry_calls == []  # terminal, no retry attempted


@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
@patch("app.workers.tasks.extract_profile_fields")
def test_transient_vision_error_retries(mock_extract, mock_get_doc, mock_preprocess, db_session):
    doc = _make_user_and_doc(db_session)
    mock_extract.side_effect = VisionExtractionError("rate limited", transient=True)

    task = _FakeTask(retries=0, exhausted=False)
    with pytest.raises(RuntimeError, match="Retry"):
        _run(task, db_session, str(doc.id))

    assert len(task.retry_calls) == 1
    db_session.refresh(doc)
    # Still mid-flight — not yet marked failed since a retry was scheduled.
    assert doc.ocr_status == "processing"


@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
@patch("app.workers.tasks.extract_profile_fields")
def test_transient_error_exhausted_retries_fails_document(
    mock_extract, mock_get_doc, mock_preprocess, db_session
):
    doc = _make_user_and_doc(db_session)
    mock_extract.side_effect = VisionExtractionError("still rate limited", transient=True)

    task = _FakeTask(retries=3, exhausted=True)
    _run(task, db_session, str(doc.id))

    db_session.refresh(doc)
    assert doc.ocr_status == "failed"
    assert doc.ocr_error == "extraction failed after retries"


@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
@patch("app.workers.tasks.extract_profile_fields")
def test_terminal_vision_error_fails_without_retry(
    mock_extract, mock_get_doc, mock_preprocess, db_session
):
    doc = _make_user_and_doc(db_session)
    mock_extract.side_effect = VisionExtractionError("bad request", transient=False)

    task = _FakeTask()
    _run(task, db_session, str(doc.id))

    db_session.refresh(doc)
    assert doc.ocr_status == "failed"
    assert task.retry_calls == []


@patch("app.workers.tasks.extract_profile_fields")
@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_rerun_is_idempotent_no_duplicate_candidates(
    mock_get_doc, mock_preprocess, mock_extract, db_session
):
    doc = _make_user_and_doc(db_session)
    mock_extract.return_value = ExtractionResult(
        type_mismatch=False,
        detected_doc_type="aadhaar",
        fields=[_grounded("full_name", "Rajesh Kumar", "Name: Rajesh Kumar", 0.85, "medium", False)],
        missing_fields=[],
    )

    _run(_FakeTask(), db_session, str(doc.id))
    _run(_FakeTask(), db_session, str(doc.id))  # re-trigger

    rows = db_session.query(ProfileField).filter_by(field_name="full_name").all()
    assert len(rows) == 1


@patch("app.workers.tasks.extract_profile_fields")
@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_format_invalid_field_status(mock_get_doc, mock_preprocess, mock_extract, db_session):
    doc = _make_user_and_doc(db_session)
    mock_extract.return_value = ExtractionResult(
        type_mismatch=False,
        detected_doc_type="aadhaar",
        fields=[
            _grounded(
                "aadhaar_number", "12345678901", "Aadhaar: 12345678901", 0.3, "low", True,
                format_valid=False,
            )
        ],
        missing_fields=[],
    )

    _run(_FakeTask(), db_session, str(doc.id))

    field = db_session.query(ProfileField).filter_by(field_name="aadhaar_number").one()
    assert field.status == "failed_validation"


@patch("app.workers.tasks.extract_profile_fields")
@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_high_confidence_non_high_stakes_auto_confirmed(
    mock_get_doc, mock_preprocess, mock_extract, db_session
):
    doc = _make_user_and_doc(db_session)
    mock_extract.return_value = ExtractionResult(
        type_mismatch=False,
        detected_doc_type="aadhaar",
        fields=[_grounded("address", "123 MG Road", "Address: 123 MG Road", 0.95, "high", False)],
        missing_fields=[],
    )

    _run(_FakeTask(), db_session, str(doc.id))

    field = db_session.query(ProfileField).filter_by(field_name="address").one()
    assert field.status == "confirmed"


@patch("app.workers.tasks.extract_profile_fields")
@patch("app.workers.tasks.preprocess", return_value=([b"page1"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_high_stakes_always_needs_confirmation_even_high_confidence(
    mock_get_doc, mock_preprocess, mock_extract, db_session
):
    doc = _make_user_and_doc(db_session)
    mock_extract.return_value = ExtractionResult(
        type_mismatch=False,
        detected_doc_type="aadhaar",
        fields=[_grounded("dob", "1998-04-12", "DOB: 1998-04-12", 0.97, "high", True)],
        missing_fields=[],
    )

    _run(_FakeTask(), db_session, str(doc.id))

    field = db_session.query(ProfileField).filter_by(field_name="dob").one()
    assert field.status == "needs_confirmation"
