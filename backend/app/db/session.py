"""SQLAlchemy engine + session factory, configured from settings.database_url.

Synchronous Session (SQLAlchemy 2.0) to stay consistent with the sync Celery workers.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session() -> Iterator[Session]:
    """Yield a session and always close it. Used by the FastAPI get_db dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
