"""FastAPI application entrypoint. Wires routers, middleware, and startup."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, documents, forms, history, profile
from app.config import settings
from app.core.logging import configure_logging, logger
from app.services.storage import ensure_bucket

configure_logging()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Dev/MinIO convenience only (SPEC-PHASE1.md §6.2) — in prod the bucket already
    # exists, and if S3 is unreachable at boot we log and continue rather than crash the
    # app; the first real upload will surface the problem loudly instead.
    try:
        ensure_bucket()
    except Exception:
        logger.warning("ensure_bucket failed at startup; continuing")
    yield


app = FastAPI(title="GovForm Auto-Filler", version="0.1.0", lifespan=_lifespan)

# Dev talks to the API same-origin via the Vite proxy, so cors_origins is empty and this
# middleware is a no-op. Prod (cross-origin split) sets cors_origins and needs credentials.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(profile.router, prefix="/api/profile", tags=["profile"])
app.include_router(forms.router, prefix="/api/forms", tags=["forms"])
app.include_router(history.router, prefix="/api/history", tags=["history"])


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness — process is up. No dependency calls."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> JSONResponse:
    """Readiness — Postgres + Redis reachable. 503 if either is down; never 500."""
    checks: dict[str, str] = {}

    try:
        from sqlalchemy import text

        from app.db.session import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "down"

    try:
        import redis

        client = redis.Redis.from_url(settings.celery_broker_url)
        client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "down"

    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
