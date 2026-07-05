"""Auth request/response contracts."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, field_validator

from app.schemas.user import UserOut


class _PasswordMixin:
    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        # bcrypt silently truncates at 72 bytes; reject rather than hash a truncated secret.
        if len(v.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 bytes")
        return v


class RegisterRequest(_PasswordMixin, BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    token: str


class ResendRequest(BaseModel):
    email: EmailStr


class MessageResponse(BaseModel):
    message: str


class TokenResponse(BaseModel):
    """Returned by /login (with user) and /refresh (user omitted)."""

    access_token: str
    token_type: str = "bearer"
    user: UserOut | None = None
