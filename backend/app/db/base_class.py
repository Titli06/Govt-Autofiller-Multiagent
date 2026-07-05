"""The declarative Base. Kept import-free so models can import it without a cycle."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
