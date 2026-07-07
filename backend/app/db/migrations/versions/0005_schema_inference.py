"""schema inference for unseen forms: forms.schema_source, form_fields.placement

Revision ID: 0005_schema_inference
Revises: 0004_verification_review
Create Date: 2026-07-07

Phase 4 — see SPEC-PHASE4.md §4.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_schema_inference"
down_revision: Union[str, None] = "0004_verification_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "forms",
        sa.Column("schema_source", sa.String(length=16), nullable=False, server_default="template"),
    )
    op.alter_column("forms", "schema_source", server_default=None)

    op.add_column("form_fields", sa.Column("placement", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("form_fields", "placement")
    op.drop_column("forms", "schema_source")
