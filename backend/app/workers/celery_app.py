"""Celery application instance (Redis broker + result backend)."""

from celery import Celery

from app.config import settings

# Import the full model aggregator (not just what workers/tasks.py happens to import
# directly) so every table is registered in Base.metadata before SQLAlchemy needs to
# resolve any FK string reference (e.g. Document.user_id -> "users.id"). Without this,
# the worker process's import graph never pulls in app.models.user, and the first query
# touching a cross-model FK crashes with NoReferencedTableError — a gap unit tests don't
# catch because conftest.py's db_engine fixture imports app.db.base directly.
import app.db.base  # noqa: E402,F401

celery_app = Celery(
    "govfill",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
