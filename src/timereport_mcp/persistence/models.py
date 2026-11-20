"""SQLAlchemy models for SMS claims, suppressions, and audit events."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SmsClaim(Base):
    __tablename__ = "sms_claims"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "employee_id",
            "period",
            "local_date",
            name="uq_sms_claim_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    employee_id: Mapped[str] = mapped_column(String(128), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    template_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    telephone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SmsSuppression(Base):
    __tablename__ = "sms_suppressions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "employee_id", name="uq_sms_suppression_employee"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    employee_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
