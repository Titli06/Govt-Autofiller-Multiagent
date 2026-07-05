"""Auth routes: register, email verification, login (JWT issue), token refresh, logout.

Session model: short-lived access JWT (returned in the body, held in memory by the SPA)
plus a DB-backed, rotating refresh token delivered as an httpOnly cookie. Email
verification is mandatory before login. See SPEC.md §6 / §10 for the behavioral rules and
the security edge cases (enumeration safety, refresh-reuse family revocation).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.core.logging import logger
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.email_verification_token import EmailVerificationToken
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResendRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.schemas.user import UserOut
from app.services.email import send_verification_email

router = APIRouter()

# Generic messages — deliberately identical across branches to avoid account enumeration.
_REGISTER_MSG = "Registration received. Check your email to verify your account."
_RESEND_MSG = "If that account exists and is unverified, a verification email has been sent."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """Normalize a possibly-naive DB datetime (SQLite) to tz-aware UTC for comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _err(status_code: int, detail: str, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"detail": detail, "code": code})


# --- Refresh cookie helpers --------------------------------------------------


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw_token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
    )


# --- Token issuance helpers --------------------------------------------------


def _issue_verification_token(db: Session, user: User) -> str:
    """Invalidate the user's prior unused verification tokens, mint a fresh one."""
    db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=_now())
    )
    raw = generate_opaque_token()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(hours=settings.email_verification_expire_hours),
        )
    )
    return raw


def _issue_refresh_token(db: Session, user: User, family_id: uuid.UUID | None = None) -> RefreshToken:
    raw = generate_opaque_token()
    token = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        family_id=family_id or uuid.uuid4(),
        expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(token)
    db.flush()  # populate token.id for rotation lineage
    token._raw = raw  # type: ignore[attr-defined]  # transient, never persisted
    return token


def _send_verification(user: User, raw_token: str) -> None:
    verify_url = f"{settings.frontend_base_url}/verify?token={raw_token}"
    send_verification_email(user.email, verify_url)


# --- Endpoints ---------------------------------------------------------------


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> MessageResponse:
    email = body.email.lower().strip()
    existing = db.scalar(select(User).where(User.email == email))

    if existing is not None:
        if existing.is_verified:
            raise _err(status.HTTP_409_CONFLICT, "Email already registered", "EMAIL_TAKEN")
        # Unverified re-registration: idempotently re-send verification, same generic reply.
        raw = _issue_verification_token(db, existing)
        db.commit()
        _send_verification(existing, raw)
        return MessageResponse(message=_REGISTER_MSG)

    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    raw = _issue_verification_token(db, user)
    db.commit()
    _send_verification(user, raw)
    logger.info("user_registered user_id=%s", user.id)
    return MessageResponse(message=_REGISTER_MSG)


@router.post("/verify-email", response_model=MessageResponse)
def verify_email(body: VerifyEmailRequest, db: Session = Depends(get_db)) -> MessageResponse:
    row = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == hash_token(body.token)
        )
    )
    if row is None or row.used_at is not None or _aware(row.expires_at) < _now():
        raise _err(
            status.HTTP_400_BAD_REQUEST,
            "Verification link is invalid or expired",
            "INVALID_OR_EXPIRED_TOKEN",
        )
    user = db.get(User, row.user_id)
    if user is None:
        raise _err(
            status.HTTP_400_BAD_REQUEST,
            "Verification link is invalid or expired",
            "INVALID_OR_EXPIRED_TOKEN",
        )
    if user.is_verified:
        row.used_at = _now()
        db.commit()
        raise _err(status.HTTP_400_BAD_REQUEST, "Email already verified", "ALREADY_VERIFIED")

    user.email_verified_at = _now()
    row.used_at = _now()
    db.commit()
    logger.info("user_verified user_id=%s", user.id)
    return MessageResponse(message="Email verified. You can now log in.")


@router.post("/resend-verification", response_model=MessageResponse)
def resend_verification(body: ResendRequest, db: Session = Depends(get_db)) -> MessageResponse:
    email = body.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    # Generic reply regardless of existence/verified state (no enumeration).
    if user is not None and not user.is_verified:
        raw = _issue_verification_token(db, user)
        db.commit()
        _send_verification(user, raw)
    return MessageResponse(message=_RESEND_MSG)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    email = body.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    # Always run a bcrypt verify (dummy hash on miss) so timing doesn't reveal existence.
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not password_ok:
        raise _err(status.HTTP_401_UNAUTHORIZED, "Invalid email or password", "INVALID_CREDENTIALS")
    if not user.is_verified:
        raise _err(status.HTTP_403_FORBIDDEN, "Email not verified", "EMAIL_NOT_VERIFIED")

    token = _issue_refresh_token(db, user)  # fresh family
    db.commit()
    _set_refresh_cookie(response, token._raw)  # type: ignore[attr-defined]
    logger.info("user_login user_id=%s", user.id)
    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    raw = request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        raise _err(status.HTTP_401_UNAUTHORIZED, "Missing refresh token", "INVALID_REFRESH")

    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if row is None or _aware(row.expires_at) < _now():
        raise _err(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token", "INVALID_REFRESH")

    # Reuse detection: a live token has revoked_at == NULL. A revoked/rotated token being
    # presented again means it was replayed (or stolen) — revoke the whole family.
    if row.revoked_at is not None or row.replaced_by is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        db.commit()
        logger.warning("refresh_reuse_detected user_id=%s family revoked", row.user_id)
        raise _err(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token", "INVALID_REFRESH")

    user = db.get(User, row.user_id)
    if user is None:
        raise _err(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token", "INVALID_REFRESH")

    # Rotate within the same family.
    new_token = _issue_refresh_token(db, user, family_id=row.family_id)
    row.revoked_at = _now()
    row.replaced_by = new_token.id
    db.commit()
    _set_refresh_cookie(response, new_token._raw)  # type: ignore[attr-defined]
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    raw = request.cookies.get(settings.refresh_cookie_name)
    if raw:
        row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
        if row is not None and row.revoked_at is None:
            # Revoke the whole family so no rotated descendant survives.
            db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=_now())
            )
            db.commit()
    # Idempotent: always clear the cookie and return 204, even without a valid session.
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response)
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
