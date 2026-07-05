"""Shared pytest fixtures: in-memory DB, test client, captured verification emails.

Phase 0 uses SQLite (StaticPool, shared in-memory) and create_all rather than Alembic —
the migration is exercised separately against Postgres in CI/compose.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.db.base import Base

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Iterator[Session]:
    TestingSession = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def sent_emails(monkeypatch) -> list[dict]:
    """Capture verification emails instead of hitting SMTP. Each entry: {to, url, token}."""
    from urllib.parse import parse_qs, urlparse

    captured: list[dict] = []

    def _capture(to: str, verify_url: str) -> None:
        token = parse_qs(urlparse(verify_url).query).get("token", [None])[0]
        captured.append({"to": to, "url": verify_url, "token": token})

    # Patch where it's used (bound name in the auth module).
    monkeypatch.setattr("app.api.routes.auth.send_verification_email", _capture)
    return captured


@pytest.fixture()
def client(db_engine, sent_emails) -> Iterator[TestClient]:
    from app.api.deps import get_db
    from app.main import app

    TestingSession = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)

    def _override_get_db() -> Iterator[Session]:
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
