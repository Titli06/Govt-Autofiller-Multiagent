"""DB-level check for the Phase 2 Form/FormField models: the (form_id, field_name)
uniqueness constraint that makes fill_form_task's delete-then-insert re-run safe."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.form import Form, FormField
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


def _field(form_id, field_name="applicant_name", **overrides) -> FormField:
    defaults = dict(
        form_id=form_id,
        field_name=field_name,
        confidence=0.9,
        confidence_band="high",
        high_stakes=False,
        transformed=False,
        needs_review=False,
        reviewed=False,
    )
    defaults.update(overrides)
    return FormField(**defaults)


def test_unique_constraint_blocks_duplicate_field_name(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    db_session.add(_field(form.id))
    db_session.commit()

    db_session.add(_field(form.id))  # same (form_id, field_name)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_distinct_field_names_on_same_form_allowed(db_session):
    user = _make_user(db_session)
    form = _make_form(db_session, user)

    db_session.add(_field(form.id, "applicant_name"))
    db_session.add(_field(form.id, "father_name"))
    db_session.commit()

    rows = db_session.query(FormField).filter_by(form_id=form.id).all()
    assert len(rows) == 2
