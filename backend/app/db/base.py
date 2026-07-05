"""Metadata aggregator: re-exports Base and imports every model so that
`Base.metadata` is fully populated for Alembic autogenerate and create_all.

Import this (not base_class) from Alembic env.py and test setup. Application/runtime code
should import Base from app.db.base_class to avoid pulling in every model.
"""

from __future__ import annotations

from app.db.base_class import Base  # noqa: F401

# Keep this list in sync as models are added.
from app.models.email_verification_token import EmailVerificationToken  # noqa: E402,F401
from app.models.refresh_token import RefreshToken  # noqa: E402,F401
from app.models.user import User  # noqa: E402,F401
