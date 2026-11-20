"""Initial SMS claims, suppressions, and audit tables.

Revision ID: 0001
Revises:
Create Date: 2026-11-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sms_claims",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("template_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("telephone_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_id",
            "period",
            "local_date",
            name="uq_sms_claim_daily",
        ),
    )
    op.create_table(
        "sms_suppressions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_id",
            name="uq_sms_suppression_employee",
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("sms_suppressions")
    op.drop_table("sms_claims")
