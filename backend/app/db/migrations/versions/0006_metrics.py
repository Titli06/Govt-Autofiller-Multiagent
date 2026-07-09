"""metrics instrumentation: form_fields.mapping_tier, pipeline_run table

Revision ID: 0006_metrics
Revises: 0005_schema_inference
Create Date: 2026-07-10

Phase 6 — see SPEC-PHASE6.md §3.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_metrics"
down_revision: Union[str, None] = "0005_schema_inference"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("form_fields", sa.Column("mapping_tier", sa.String(length=16), nullable=True))

    op.create_table(
        "pipeline_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("form_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("schema_source", sa.String(length=16), nullable=False),
        sa.Column("terminal_status", sa.String(length=32), nullable=False),
        sa.Column("fill_latency_ms", sa.Integer(), nullable=True),
        sa.Column("review_latency_ms", sa.Integer(), nullable=True),
        sa.Column("total_fields", sa.Integer(), nullable=False),
        sa.Column("autofilled_fields", sa.Integer(), nullable=False),
        sa.Column("reviewed_fields", sa.Integer(), nullable=True),
        sa.Column("approved_as_is", sa.Integer(), nullable=True),
        sa.Column("corrected_fields", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["form_id"], ["forms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("form_id", name="uq_pipeline_run_form_id"),
    )
    op.create_index("ix_pipeline_run_form_id", "pipeline_run", ["form_id"])
    op.create_index("ix_pipeline_run_user_id", "pipeline_run", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_run_user_id", table_name="pipeline_run")
    op.drop_index("ix_pipeline_run_form_id", table_name="pipeline_run")
    op.drop_table("pipeline_run")
    op.drop_column("form_fields", "mapping_tier")
