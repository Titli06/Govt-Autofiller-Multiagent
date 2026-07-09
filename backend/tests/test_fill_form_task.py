"""fill_form_task's business logic (_run_fill), exercised directly against a fake
Celery task object — mirrors test_ocr_task.py's pattern. get_document, preprocess, and
classify_form are mocked; the LangGraph pipeline itself runs for real against a seeded
Profile/ProfileField snapshot (no DB/crypto inside the graph — see agent/graph.py).

Phase 3 (SPEC-PHASE3.md §6.5): _seed_profile_field seeds a source snippet that
trivially contains the raw candidate value by default, so document_verification's
deterministic re-ground passes (exact or semantic) without ever calling the vision-LLM
verifier — keeping most of these tests free of network calls. Tests that need to
exercise the LLM-escalation path seed an empty snippet and patch
`app.workers.tasks.verify_value_on_document` explicitly. Status is now `in_review`
(any field outstanding) or `approved` (none) — `filled` is retired."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from celery.exceptions import MaxRetriesExceededError

from app.config import settings
from app.core.encryption import build_aad, decrypt_field, encrypt_field
from app.models.document import Document
from app.models.form import Form, FormField
from app.models.metrics import PipelineRun
from app.models.profile import Profile, ProfileField
from app.models.user import User
from app.services.form_placement.document_ai import DetectedField, DocumentAIError
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
    db_session,
    user,
    field_name,
    value,
    *,
    confidence=0.95,
    status="confirmed",
    snippet="__default__",
    origin="document",
    source_doc=True,
) -> ProfileField:
    profile = db_session.query(Profile).filter_by(user_id=user.id).one_or_none()
    if profile is None:
        profile = Profile(user_id=user.id)
        db_session.add(profile)
        db_session.flush()

    doc_id = None
    if source_doc:
        doc = Document(
            user_id=user.id, declared_doc_type="aadhaar", s3_key="documents/x/y.jpg", ocr_status="extracted"
        )
        db_session.add(doc)
        db_session.flush()
        doc_id = doc.id

    # Trivially self-verifying by default (snippet contains the raw value) so
    # document_verification's deterministic re-ground passes without an LLM call.
    # Pass snippet=None explicitly to force escalation.
    if snippet == "__default__":
        snippet = value

    band = "high" if confidence >= 0.9 else "medium" if confidence >= 0.7 else "low"
    aad = build_aad(profile.id, field_name)
    pf = ProfileField(
        profile_id=profile.id,
        source_doc_id=doc_id,
        origin=origin,
        field_name=field_name,
        value_encrypted=encrypt_field(value, aad=aad),
        source_snippet_encrypted=encrypt_field(snippet, aad=aad) if snippet else None,
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
    assert form.status == "in_review"  # father_name/address/aadhaar/annual_income are unfilled
    assert form.filled_at is not None
    assert form.detected_form_type == "income_certificate"

    rows = db_session.query(FormField).filter_by(form_id=form.id).all()
    assert len(rows) == 6
    by_name = {r.field_name: r for r in rows}
    aad = build_aad(form.id, "applicant_name")
    assert decrypt_field(by_name["applicant_name"].value_encrypted, aad=aad) == "Ravi Kumar"
    assert by_name["applicant_name"].verified is True
    assert by_name["applicant_name"].verification_method == "exact"
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
    assert field.verified is True  # exact snippet match, independent of candidate trust


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
    assert field.verified is True  # snippet "1998-04-12" parses to the same calendar day
    assert field.confidence == 1.0
    assert field.needs_review is True
    assert field.review_reason == "high_stakes"


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_no_profile_all_fields_flagged_missing_lands_in_review(
    mock_get_doc, mock_preprocess, mock_classify, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    rows = db_session.query(FormField).filter_by(form_id=form.id).all()
    assert len(rows) == 6
    assert all(r.value_encrypted is None for r in rows)
    assert all(r.needs_review for r in rows)
    assert all(r.verified is False for r in rows)
    db_session.refresh(form)
    assert form.status == "in_review"


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
    assert form.status == "in_review"
    assert form.detected_form_type == "unknown"


@patch("app.workers.tasks.map_field_labels", return_value={})
@patch("app.workers.tasks.detect_fields", return_value=[])
@patch("app.workers.tasks.classify_form", return_value="unknown")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_unknown_declared_form_type_with_zero_detected_fields_fails_terminal(
    mock_get_doc, mock_preprocess, mock_classify, mock_detect, mock_map, db_session
):
    """Phase 4 (SPEC-PHASE4.md Decision 4): an unknown declared form_type is no longer
    an immediate 'unsupported form type' TemplateError (the upload gate now accepts
    it and routes to inference) — but zero detected fields still fails cleanly,
    never a vacuous empty draft (Decision 5)."""
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="passport_renewal")

    task = _FakeTask()
    _run_fill(task, db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "failed"
    assert form.fill_error == "could not detect any fields on this form"
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


# --- Phase 3: verification escalation, manual candidates, skew guard ------------------


@patch("app.workers.tasks.verify_value_on_document")
@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_deterministic_miss_escalates_to_llm_and_fails_verification(
    mock_get_doc, mock_preprocess, mock_classify, mock_verify, db_session
):
    mock_verify.return_value = False
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "full_name", "Ravi Kumar", snippet=None)
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    field = db_session.query(FormField).filter_by(form_id=form.id, field_name="applicant_name").one()
    assert field.verified is False
    assert field.verification_method == "llm"
    assert field.review_reason == "verification_failed"
    mock_verify.assert_called_once()


@patch("app.workers.tasks.verify_value_on_document")
@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_transient_verification_error_retries(
    mock_get_doc, mock_preprocess, mock_classify, mock_verify, db_session
):
    mock_verify.side_effect = VisionExtractionError("rate limited", transient=True)
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "full_name", "Ravi Kumar", snippet=None)
    form = _make_form(db_session, user)

    task = _FakeTask(retries=0, exhausted=False)
    with pytest.raises(RuntimeError, match="Retry"):
        _run_fill(task, db_session, str(form.id))

    assert len(task.retry_calls) == 1
    db_session.refresh(form)
    assert form.status == "processing"


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_manual_candidate_included_via_outer_join(mock_get_doc, mock_preprocess, mock_classify, db_session):
    user = _make_user(db_session)
    _seed_profile_field(
        db_session, user, "full_name", "Ravi Kumar", origin="manual", source_doc=False, status="user_corrected"
    )
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    field = db_session.query(FormField).filter_by(form_id=form.id, field_name="applicant_name").one()
    aad = build_aad(form.id, "applicant_name")
    assert decrypt_field(field.value_encrypted, aad=aad) == "Ravi Kumar"
    assert field.source_doc_id is None
    assert field.confidence == 1.0  # user-acted candidate


@patch("app.workers.tasks.estimate_skew", return_value=12.0)
@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_significant_skew_sets_placement_warning(
    mock_get_doc, mock_preprocess, mock_classify, mock_skew, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.skew_angle == 12.0
    assert form.placement_warning is not None
    assert "rotated" in form.placement_warning


@patch("app.workers.tasks.estimate_skew", return_value=1.0)
@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_upright_scan_no_placement_warning(mock_get_doc, mock_preprocess, mock_classify, mock_skew, db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.skew_angle is None
    assert form.placement_warning is None


@patch("app.workers.tasks.estimate_skew", side_effect=RuntimeError("opencv blew up"))
@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_skew_detector_failure_is_swallowed_fill_still_completes(
    mock_get_doc, mock_preprocess, mock_classify, mock_skew, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.status in ("in_review", "approved")
    assert form.placement_warning is None


# --- Phase 4: schema inference for unseen forms (SPEC-PHASE4.md §6.7) -----------------


@patch("app.workers.tasks.map_field_labels")
@patch("app.workers.tasks.detect_fields")
@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_confident_detection_of_known_type_uses_template_path_never_calls_document_ai(
    mock_get_doc, mock_preprocess, mock_classify, mock_detect, mock_map, db_session
):
    """Decision 2: a confident classify_form detection of a KNOWN type overrides an
    unseen declared label — the template path is used, and Document AI/label mapper
    are never even called."""
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="marriage_certificate")

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.schema_source == "template"
    assert form.detected_form_type == "income_certificate"
    assert form.status in ("in_review", "approved")
    mock_detect.assert_not_called()
    mock_map.assert_not_called()
    rows = db_session.query(FormField).filter_by(form_id=form.id).all()
    field_names = {r.field_name for r in rows}
    assert field_names == {
        "applicant_name", "father_name", "date_of_birth", "address", "annual_income", "aadhaar_number",
    }


@patch("app.workers.tasks.map_field_labels")
@patch("app.workers.tasks.detect_fields")
@patch("app.workers.tasks.classify_form", return_value="unknown")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_unrecognized_form_infers_schema_and_lands_in_review(
    mock_get_doc, mock_preprocess, mock_classify, mock_detect, mock_map, db_session
):
    mock_detect.return_value = [
        DetectedField(name="Father's Name", page=1, value_bbox=(0.1, 0.1, 0.6, 0.15), confidence=0.9),
    ]
    mock_map.return_value = {"Father's Name": {"profile_key": "father_name", "tier": "exact"}}

    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "father_name", "Suresh Kumar")
    form = _make_form(db_session, user, form_type="marriage_certificate")

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.schema_source == "inferred"
    assert form.status == "in_review"  # EVERY inferred field always needs review (Decision 1)

    field = db_session.query(FormField).filter_by(form_id=form.id, field_name="father_s_name").one()
    aad = build_aad(form.id, "father_s_name")
    assert decrypt_field(field.value_encrypted, aad=aad) == "Suresh Kumar"
    assert field.verified is True  # exact snippet match
    assert field.confidence == settings.map_cap_exact  # tier-capped, never promoted to high
    assert field.needs_review is True
    assert field.review_reason == "inferred_mapping"
    assert field.placement == {"page": 1, "bbox": [0.1, 0.1, 0.6, 0.15]}


@patch("app.workers.tasks.map_field_labels", return_value={})
@patch("app.workers.tasks.detect_fields", return_value=[])
@patch("app.workers.tasks.classify_form", return_value="unknown")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_zero_detected_fields_fails_never_a_vacuous_approved_form(
    mock_get_doc, mock_preprocess, mock_classify, mock_detect, mock_map, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="marriage_certificate")

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "failed"
    assert form.fill_error == "could not detect any fields on this form"
    assert db_session.query(FormField).filter_by(form_id=form.id).count() == 0


@patch("app.workers.tasks.map_field_labels")
@patch("app.workers.tasks.detect_fields")
@patch("app.workers.tasks.classify_form", return_value="unknown")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_document_ai_transient_error_retries(
    mock_get_doc, mock_preprocess, mock_classify, mock_detect, mock_map, db_session
):
    mock_detect.side_effect = DocumentAIError("service unavailable", transient=True)
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="marriage_certificate")

    task = _FakeTask(retries=0, exhausted=False)
    with pytest.raises(RuntimeError, match="Retry"):
        _run_fill(task, db_session, str(form.id))

    assert len(task.retry_calls) == 1
    db_session.refresh(form)
    assert form.status == "processing"


@patch("app.workers.tasks.map_field_labels")
@patch("app.workers.tasks.detect_fields")
@patch("app.workers.tasks.classify_form", return_value="unknown")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_document_ai_terminal_error_fails_without_retry(
    mock_get_doc, mock_preprocess, mock_classify, mock_detect, mock_map, db_session
):
    mock_detect.side_effect = DocumentAIError("bad config", transient=False)
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="marriage_certificate")

    task = _FakeTask()
    _run_fill(task, db_session, str(form.id))

    db_session.refresh(form)
    assert form.status == "failed"
    assert task.retry_calls == []


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_template_field_placement_never_persisted_even_though_spec_has_one(
    mock_get_doc, mock_preprocess, mock_classify, db_session
):
    """Regression: a template field's spec.placement is the template's OWN
    {"page","x","y"} coordinate shape (copied through by profile_lookup_tool), which
    must NEVER land on FormField.placement — that column is inferred-forms-only
    (Decision 7). The renderer reads a template field's placement from the template
    JSON, not the row."""
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "full_name", "Ravi Kumar")
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    assert form.schema_source == "template"
    rows = db_session.query(FormField).filter_by(form_id=form.id).all()
    assert all(r.placement is None for r in rows)


# --- Phase 6: metrics instrumentation (SPEC-PHASE6.md §9) -----------------------------


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_successful_fill_writes_pipeline_run_row(mock_get_doc, mock_preprocess, mock_classify, db_session):
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "full_name", "Ravi Kumar")
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    db_session.refresh(form)
    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.user_id == user.id
    assert run.schema_source == "template"
    assert run.terminal_status == form.status == "in_review"
    assert run.fill_latency_ms is not None and run.fill_latency_ms >= 0
    assert run.total_fields == 6
    assert run.autofilled_fields == 1  # only applicant_name is filled and not flagged
    assert run.review_latency_ms is None  # still in_review, review span not yet known


@patch("app.workers.tasks.classify_form", return_value="scholarship_application")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_type_mismatch_writes_zero_count_pipeline_run_row(
    mock_get_doc, mock_preprocess, mock_classify, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="income_certificate")

    _run_fill(_FakeTask(), db_session, str(form.id))

    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.terminal_status == "type_mismatch"
    assert run.total_fields == 0
    assert run.autofilled_fields == 0


@patch("app.workers.tasks.map_field_labels", return_value={})
@patch("app.workers.tasks.detect_fields", return_value=[])
@patch("app.workers.tasks.classify_form", return_value="unknown")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_failed_fill_writes_zero_count_pipeline_run_row(
    mock_get_doc, mock_preprocess, mock_classify, mock_detect, mock_map, db_session
):
    user = _make_user(db_session)
    form = _make_form(db_session, user, form_type="passport_renewal")

    _run_fill(_FakeTask(), db_session, str(form.id))

    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.terminal_status == "failed"
    assert run.total_fields == 0


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_rerun_upserts_pipeline_run_not_duplicates(mock_get_doc, mock_preprocess, mock_classify, db_session):
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "full_name", "Ravi Kumar")
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))
    _run_fill(_FakeTask(), db_session, str(form.id))  # re-trigger

    assert db_session.query(PipelineRun).filter_by(form_id=form.id).count() == 1


@patch("app.workers.tasks.map_field_labels")
@patch("app.workers.tasks.detect_fields")
@patch("app.workers.tasks.classify_form", return_value="unknown")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_inferred_fill_persists_mapping_tier(
    mock_get_doc, mock_preprocess, mock_classify, mock_detect, mock_map, db_session
):
    mock_detect.return_value = [
        DetectedField(name="Father's Name", page=1, value_bbox=(0.1, 0.1, 0.6, 0.15), confidence=0.9),
    ]
    mock_map.return_value = {"Father's Name": {"profile_key": "father_name", "tier": "exact"}}

    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "father_name", "Suresh Kumar")
    form = _make_form(db_session, user, form_type="marriage_certificate")

    _run_fill(_FakeTask(), db_session, str(form.id))

    field = db_session.query(FormField).filter_by(form_id=form.id, field_name="father_s_name").one()
    assert field.mapping_tier == "exact"


@patch("app.workers.tasks.classify_form", return_value="income_certificate")
@patch("app.workers.tasks.preprocess", return_value=([b"page"], 1))
@patch("app.workers.tasks.get_document", return_value=b"raw bytes")
def test_template_fill_leaves_mapping_tier_null(mock_get_doc, mock_preprocess, mock_classify, db_session):
    user = _make_user(db_session)
    _seed_profile_field(db_session, user, "full_name", "Ravi Kumar")
    form = _make_form(db_session, user)

    _run_fill(_FakeTask(), db_session, str(form.id))

    rows = db_session.query(FormField).filter_by(form_id=form.id).all()
    assert all(r.mapping_tier is None for r in rows)
