"""profile ingestion: documents, profiles, profile_fields

Revision ID: 0002_profile_ingestion
Revises: 0001_initial_auth
Create Date: 2026-07-06

Phase 1 — see SPEC-PHASE1.md §4.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_profile_ingestion"
down_revision: Union[str, None] = "0001_initial_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("declared_doc_type", sa.String(length=32), nullable=False),
        sa.Column("detected_doc_type", sa.String(length=32), nullable=True),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("ocr_status", sa.String(length=32), nullable=False),
        sa.Column("ocr_error", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])

    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"], unique=True)

    op.create_table(
        "profile_fields",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("source_doc_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("corrected_value_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("value_masked", sa.String(length=64), nullable=True),
        sa.Column("source_snippet_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_band", sa.String(length=16), nullable=False),
        sa.Column("high_stakes", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("validators", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_doc_id"], ["documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "profile_id", "field_name", "source_doc_id", name="uq_profile_field_candidate"
        ),
    )
    op.create_index("ix_profile_fields_profile_id", "profile_fields", ["profile_id"])
    op.create_index(
        "ix_profile_fields_profile_field_name", "profile_fields", ["profile_id", "field_name"]
    )


def downgrade() -> None:
    op.drop_table("profile_fields")
    op.drop_index("ix_profiles_user_id", table_name="profiles")
    op.drop_table("profiles")
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_table("documents")
