"""known-template form fill: forms, form_fields

Revision ID: 0003_forms
Revises: 0002_profile_ingestion
Create Date: 2026-07-06

Phase 2 — see SPEC-PHASE2.md §4.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_forms"
down_revision: Union[str, None] = "0002_profile_ingestion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "forms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("declared_form_type", sa.String(length=64), nullable=False),
        sa.Column("detected_form_type", sa.String(length=64), nullable=True),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fill_error", sa.Text(), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_forms_user_id", "forms", ["user_id"])

    op.create_table(
        "form_fields",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("form_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("profile_key", sa.String(length=64), nullable=True),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("value_masked", sa.String(length=64), nullable=True),
        sa.Column("profile_field_id", sa.Uuid(), nullable=True),
        sa.Column("source_doc_id", sa.Uuid(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_band", sa.String(length=16), nullable=False),
        sa.Column("high_stakes", sa.Boolean(), nullable=False),
        sa.Column("transformed", sa.Boolean(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("review_reason", sa.String(length=32), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("flags", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["form_id"], ["forms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_field_id"], ["profile_fields.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_doc_id"], ["documents.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("form_id", "field_name", name="uq_form_field_name"),
    )
    op.create_index("ix_form_fields_form_id", "form_fields", ["form_id"])


def downgrade() -> None:
    op.drop_table("form_fields")
    op.drop_index("ix_forms_user_id", table_name="forms")
    op.drop_table("forms")
