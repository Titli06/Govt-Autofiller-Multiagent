"""Celery application instance (Redis broker + result backend)."""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "govfill",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
