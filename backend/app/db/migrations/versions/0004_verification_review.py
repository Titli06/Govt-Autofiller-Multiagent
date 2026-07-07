"""verification + HITL review: forms/form_fields additions, profile_fields relaxation

Revision ID: 0004_verification_review
Revises: 0003_forms
Create Date: 2026-07-07

Phase 3 — see SPEC-PHASE3.md §4.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_verification_review"
down_revision: Union[str, None] = "0003_forms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("forms", sa.Column("rendered_s3_key", sa.Text(), nullable=True))
    op.add_column("forms", sa.Column("skew_angle", sa.Float(), nullable=True))
    op.add_column("forms", sa.Column("placement_warning", sa.Text(), nullable=True))

    op.add_column(
        "form_fields",
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("form_fields", sa.Column("verification_method", sa.String(length=16), nullable=True))
    op.add_column(
        "form_fields", sa.Column("corrected_value_encrypted", sa.LargeBinary(), nullable=True)
    )
    op.add_column("form_fields", sa.Column("review_action", sa.String(length=16), nullable=True))
    op.add_column(
        "form_fields", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.alter_column("form_fields", "verified", server_default=None)

    # profile_fields: relax source_doc_id to support manual write-back candidates
    # (Decision 11) and tag provenance with a new origin column.
    op.alter_column("profile_fields", "source_doc_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column(
        "profile_fields",
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="document"),
    )
    op.alter_column("profile_fields", "origin", server_default=None)


def downgrade() -> None:
    op.drop_column("profile_fields", "origin")
    op.alter_column("profile_fields", "source_doc_id", existing_type=sa.Uuid(), nullable=False)

    op.drop_column("form_fields", "reviewed_at")
    op.drop_column("form_fields", "review_action")
    op.drop_column("form_fields", "corrected_value_encrypted")
    op.drop_column("form_fields", "verification_method")
    op.drop_column("form_fields", "verified")

    op.drop_column("forms", "placement_warning")
    op.drop_column("forms", "skew_angle")
    op.drop_column("forms", "rendered_s3_key")
