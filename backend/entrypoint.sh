#!/bin/sh
# API container entrypoint: apply DB migrations, then start the server.
# Only the api service runs this (the worker starts celery directly) so migrations
# run exactly once, with no race between containers.
set -e

echo "[entrypoint] Applying database migrations..."
alembic upgrade head

echo "[entrypoint] Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
