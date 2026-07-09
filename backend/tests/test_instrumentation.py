"""metrics/instrumentation.py: record_fill (upsert at fill completion) and
record_review (update at approval) — exercised directly against a seeded Form/
FormField/PipelineRun, mirroring test_fill_form_task.py's pattern (SPEC-PHASE6.md §9)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.metrics.instrumentation import record_fill, record_review
from app.models.form import Form, FormField
from app.models.metrics import PipelineRun
from app.models.user import User


def _make_user(db_session) -> User:
    user = User(email="u@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def _make_form(db_session, user, **overrides) -> Form:
    defaults = dict(
        user_id=user.id,
        declared_form_type="income_certificate",
        s3_key="forms/x/y.jpg",
        status="pending",
        schema_source="template",
    )
    defaults.update(overrides)
    form = Form(**defaults)
    db_session.add(form)
    db_session.commit()
    return form


def _field_result(field_name="applicant_name", *, needs_review=False) -> dict:
    return {"field_name": field_name, "needs_review": needs_review}


def _add_field(db_session, form_id, field_name, *, needs_review=False, review_action=None) -> FormField:
    field = FormField(
        form_id=form_id,
        field_name=field_name,
        confidence=0.9,
        confidence_band="high",
        high_stakes=False,
        transformed=False,
        needs_review=needs_review,
        reviewed=review_action is not None,
        review_action=review_action,
    )
    db_session.add(field)
    db_session.flush()
    return field


# --- record_fill -------------------------------------------------------------------


def test_record_fill_success_writes_row_with_latency_and_counts(db_session):
    user = _make_user(db_session)
    base = datetime.now(timezone.utc)
    form = _make_form(db_session, user, created_at=base, status="approved")
    form.filled_at = base + timedelta(seconds=4)

    record_fill(db_session, form, [_field_result("a"), _field_result("b", needs_review=True)])
    db_session.commit()

    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.user_id == user.id
    assert run.schema_source == "template"
    assert run.terminal_status == "approved"
    assert run.fill_latency_ms == 4000
    assert run.total_fields == 2
    assert run.autofilled_fields == 1


def test_record_fill_auto_approved_zeroes_review_span(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, status="approved")
    form.filled_at = datetime.now(timezone.utc)

    record_fill(db_session, form, [_field_result("a")])
    db_session.commit()

    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.review_latency_ms == 0
    assert run.reviewed_fields == 0
    assert run.approved_as_is == 0
    assert run.corrected_fields == 0


def test_record_fill_in_review_leaves_review_span_null(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, status="in_review")
    form.filled_at = datetime.now(timezone.utc)

    record_fill(db_session, form, [_field_result("a", needs_review=True)])
    db_session.commit()

    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.review_latency_ms is None
    assert run.reviewed_fields is None


def test_record_fill_failure_writes_zero_count_row(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, status="failed")
    form.filled_at = datetime.now(timezone.utc)

    record_fill(db_session, form, None)
    db_session.commit()

    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.terminal_status == "failed"
    assert run.total_fields == 0
    assert run.autofilled_fields == 0


def test_record_fill_rerun_upserts_not_duplicates(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, status="approved")
    form.filled_at = datetime.now(timezone.utc)

    record_fill(db_session, form, [_field_result("a")])
    db_session.commit()
    record_fill(db_session, form, [_field_result("a"), _field_result("b")])
    db_session.commit()

    assert db_session.query(PipelineRun).filter_by(form_id=form.id).count() == 1
    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.total_fields == 2


# --- record_review -------------------------------------------------------------------


def test_record_review_sets_latency_and_action_counts(db_session):
    user = _make_user(db_session)
    base = datetime.now(timezone.utc)
    form = _make_form(db_session, user, status="in_review")
    form.filled_at = base - timedelta(seconds=10)
    record_fill(db_session, form, [_field_result("a", needs_review=True), _field_result("b", needs_review=True)])
    db_session.commit()

    _add_field(db_session, form.id, "a", needs_review=True, review_action="approved")
    _add_field(db_session, form.id, "b", needs_review=True, review_action="corrected")
    db_session.commit()

    form.status = "approved"
    record_review(db_session, form)
    db_session.commit()

    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.terminal_status == "approved"
    assert run.review_latency_ms is not None and run.review_latency_ms > 0
    assert run.reviewed_fields == 2
    assert run.approved_as_is == 1
    assert run.corrected_fields == 1


def test_record_review_counts_approve_blank_as_approved_as_is(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, status="in_review")
    form.filled_at = datetime.now(timezone.utc)
    record_fill(db_session, form, [_field_result("a", needs_review=True)])
    db_session.commit()

    _add_field(db_session, form.id, "a", needs_review=True, review_action="approved_blank")
    db_session.commit()

    form.status = "approved"
    record_review(db_session, form)
    db_session.commit()

    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.approved_as_is == 1
    assert run.corrected_fields == 0


def test_record_review_reopen_reapprove_overwrites_not_appends(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, status="in_review")
    form.filled_at = datetime.now(timezone.utc)
    record_fill(db_session, form, [_field_result("a", needs_review=True)])
    db_session.commit()

    field = _add_field(db_session, form.id, "a", needs_review=True, review_action="approved")
    db_session.commit()

    form.status = "approved"
    record_review(db_session, form)
    db_session.commit()

    # Reopen: a correction flips the field and the form back to in_review.
    field.review_action = "corrected"
    form.status = "in_review"
    db_session.commit()

    # Re-approve.
    form.status = "approved"
    record_review(db_session, form)
    db_session.commit()

    assert db_session.query(PipelineRun).filter_by(form_id=form.id).count() == 1
    run = db_session.query(PipelineRun).filter_by(form_id=form.id).one()
    assert run.approved_as_is == 0
    assert run.corrected_fields == 1


def test_record_review_no_row_is_safe_noop(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user, status="approved")
    form.filled_at = datetime.now(timezone.utc)
    db_session.commit()  # no record_fill ever called -> no pipeline_run row

    record_review(db_session, form)  # must not raise
    db_session.commit()

    assert db_session.query(PipelineRun).filter_by(form_id=form.id).count() == 0
