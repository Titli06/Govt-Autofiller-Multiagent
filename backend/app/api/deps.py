"""Shared FastAPI dependencies: DB session, current authenticated user (JWT)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_access_token
from app.db.session import get_session
from app.models.document import Document
from app.models.user import User


def get_db() -> Iterator[Session]:
    yield from get_session()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"detail": "Missing bearer token", "code": "INVALID_TOKEN"},
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(token)
    except TokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"detail": "Invalid or expired token", "code": "INVALID_TOKEN"},
        )
    user = db.get(User, _as_uuid(claims["sub"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"detail": "User not found", "code": "INVALID_TOKEN"},
        )
    return user


def get_owned_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Document:
    """Loads a Document scoped to the requesting user. Cross-user access returns 404
    (not 403) so a request can't distinguish "not yours" from "doesn't exist"."""
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"detail": "Document not found", "code": "NOT_FOUND"},
        )
    return doc


def _as_uuid(value: str):
    import uuid

    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"detail": "Invalid subject", "code": "INVALID_TOKEN"},
        )
