"""FastAPI application entrypoint. Wires routers, middleware, and startup."""

from fastapi import FastAPI

from app.api.routes import auth, documents, forms, history, profile

app = FastAPI(title="GovForm Auto-Filler", version="0.1.0")

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(profile.router, prefix="/api/profile", tags=["profile"])
app.include_router(forms.router, prefix="/api/forms", tags=["forms"])
app.include_router(history.router, prefix="/api/history", tags=["history"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
