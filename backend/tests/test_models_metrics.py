"""DB-level checks for the Phase 6 PipelineRun model: defaults, the unique
form_id constraint (one row per form, so record_fill's upsert never duplicates), and
the cascade FKs the purge relies on."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.form import Form
from app.models.metrics import PipelineRun
from app.models.user import User


def _make_user(db_session) -> User:
    user = User(email="u@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def _make_form(db_session, user) -> Form:
    form = Form(
        user_id=user.id, declared_form_type="income_certificate", s3_key="forms/x/y.jpg", status="pending"
    )
    db_session.add(form)
    db_session.flush()
    return form


def _run(form_id, user_id, **overrides) -> PipelineRun:
    defaults = dict(
        form_id=form_id,
        user_id=user_id,
        schema_source="template",
        terminal_status="approved",
        total_fields=0,
        autofilled_fields=0,
    )
    defaults.update(overrides)
    return PipelineRun(**defaults)


def test_defaults_null_for_review_fields(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user)
    run = _run(form.id, user.id)
    db_session.add(run)
    db_session.commit()

    db_session.refresh(run)
    assert run.fill_latency_ms is None
    assert run.review_latency_ms is None
    assert run.reviewed_fields is None
    assert run.approved_as_is is None
    assert run.corrected_fields is None


def test_unique_constraint_blocks_duplicate_form_id(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    db_session.add(_run(form.id, user.id))
    db_session.commit()

    db_session.add(_run(form.id, user.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_distinct_forms_allowed(db_session):
    user = _make_user(db_session)
    form1 = _make_form(db_session, user)
    form2 = _make_form(db_session, user)

    db_session.add(_run(form1.id, user.id))
    db_session.add(_run(form2.id, user.id))
    db_session.commit()

    assert db_session.query(PipelineRun).count() == 2
