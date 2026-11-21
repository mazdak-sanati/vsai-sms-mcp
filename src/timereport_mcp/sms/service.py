"""On-demand SMS send with durable daily idempotency.

Destination and message body are supplied by the caller (agent). This module
does not read workbooks or server-side SMS templates for send content.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from timereport_mcp.auth.principals import Principal
from timereport_mcp.config import AppConfig
from timereport_mcp.excel.service import validate_period
from timereport_mcp.models import ToolResult
from timereport_mcp.persistence.models import SmsClaim, SmsSuppression
from timereport_mcp.sms.gateway import SmsGateway, SmsSendRequest
from timereport_mcp.sms.phone import normalize_e164, telephone_hash

# Stored on claims for schema compatibility; content is caller-provided.
CALLER_PROVIDED_TEMPLATE_ID = "caller_provided"
_MAX_MESSAGE_CHARS = 480


def _local_today(tz_name: str, clock: date | None = None) -> date:
    if clock is not None:
        return clock
    return datetime.now(ZoneInfo(tz_name)).date()


def _claim_payload(
    claim: SmsClaim,
    *,
    replayed: bool = False,
    provider_detail: str | None = None,
    destination_last4: str | None = None,
) -> dict:
    payload = {
        "tenant_id": claim.tenant_id,
        "employee_id": claim.employee_id,
        "period": claim.period,
        "local_date": claim.local_date.isoformat(),
        "template_id": claim.template_id,
        "state": claim.state,
        "provider": claim.provider,
        "provider_message_id": claim.provider_message_id,
        "replayed": replayed,
    }
    if provider_detail:
        payload["provider_detail"] = provider_detail
    if destination_last4:
        payload["destination_last4"] = destination_last4
    return payload


def get_sms_status(
    config: AppConfig,
    session_factory: sessionmaker[Session],
    principal: Principal,
    *,
    employee_id: str,
    period: str,
    local_date: date | None = None,
) -> ToolResult:
    period_err = validate_period(period)
    if period_err is not None:
        return period_err
    tenant = config.tenants[principal.tenant_id]
    day = local_date or _local_today(tenant.timezone)
    with session_factory() as session:
        claim = session.scalar(
            select(SmsClaim).where(
                SmsClaim.tenant_id == principal.tenant_id,
                SmsClaim.employee_id == employee_id,
                SmsClaim.period == period,
                SmsClaim.local_date == day,
            )
        )
        suppression = session.scalar(
            select(SmsSuppression).where(
                SmsSuppression.tenant_id == principal.tenant_id,
                SmsSuppression.employee_id == employee_id,
            )
        )
        return ToolResult.success(
            {
                "employee_id": employee_id,
                "period": period,
                "local_date": day.isoformat(),
                "claim": None if claim is None else _claim_payload(claim),
                "suppression": None
                if suppression is None
                else {
                    "reason": suppression.reason,
                    "active": suppression.active,
                },
            }
        )


def set_sms_suppression(
    config: AppConfig,
    session_factory: sessionmaker[Session],
    principal: Principal,
    *,
    employee_id: str,
    reason: str,
    active: bool,
) -> ToolResult:
    if reason not in ("opt_out", "admin"):
        return ToolResult.failure("validation_error", "reason must be opt_out or admin")
    if reason == "opt_out" and active is False:
        return ToolResult.failure(
            "forbidden",
            "opt_out cannot be cleared through this tool.",
        )
    now = datetime.now(UTC)
    with session_factory() as session:
        row = session.scalar(
            select(SmsSuppression).where(
                SmsSuppression.tenant_id == principal.tenant_id,
                SmsSuppression.employee_id == employee_id,
            )
        )
        if row is None:
            row = SmsSuppression(
                tenant_id=principal.tenant_id,
                employee_id=employee_id,
                reason=reason,
                active=active,
                updated_at=now,
            )
            session.add(row)
        else:
            if row.reason == "opt_out" and row.active and reason == "admin" and not active:
                # Clearing admin must not clear an active opt_out; require matching reason.
                pass
            if row.reason == "opt_out" and row.active and not active:
                return ToolResult.failure(
                    "forbidden",
                    "opt_out cannot be cleared through this tool.",
                )
            row.reason = reason
            row.active = active
            row.updated_at = now
        session.commit()
        return ToolResult.success(
            {
                "employee_id": employee_id,
                "reason": row.reason,
                "active": row.active,
            }
        )


def send_sms(
    config: AppConfig,
    session_factory: sessionmaker[Session],
    gateway: SmsGateway,
    principal: Principal,
    *,
    to: str,
    message: str,
    employee_id: str,
    period: str,
    clock: date | None = None,
) -> ToolResult:
    """Send an SMS using caller-provided destination and body.

    employee_id + period are used only for suppression and same-day idempotency.
    """
    period_err = validate_period(period)
    if period_err is not None:
        return period_err

    if not employee_id or not str(employee_id).strip():
        return ToolResult.failure("validation_error", "employee_id is required")

    body = (message or "").strip()
    if not body:
        return ToolResult.failure("validation_error", "message is required")
    if len(body) > _MAX_MESSAGE_CHARS:
        return ToolResult.failure(
            "validation_error",
            f"message exceeds {_MAX_MESSAGE_CHARS} characters",
        )

    tenant = config.tenants[principal.tenant_id]
    e164 = normalize_e164(to, region=tenant.region)
    if e164 is None:
        return ToolResult.failure(
            "sms_not_eligible",
            "Telephone number could not be normalized to E.164.",
        )

    local_day = _local_today(tenant.timezone, clock)
    now = datetime.now(UTC)

    # Suppression check before claim insert.
    with session_factory() as session:
        suppression = session.scalar(
            select(SmsSuppression).where(
                SmsSuppression.tenant_id == principal.tenant_id,
                SmsSuppression.employee_id == employee_id,
                SmsSuppression.active.is_(True),
            )
        )
        if suppression is not None:
            return ToolResult.failure(
                "sms_suppressed",
                "SMS suppressed for this employee.",
                details={"reason": suppression.reason},
            )

        # Budget: count claims for the local day.
        used = session.scalar(
            select(func.count())
            .select_from(SmsClaim)
            .where(
                SmsClaim.tenant_id == principal.tenant_id,
                SmsClaim.local_date == local_day,
                SmsClaim.state.in_(("pending", "sent", "rejected", "uncertain")),
            )
        )
        if used is not None and used >= tenant.max_sends_per_tenant_per_local_day:
            return ToolResult.failure(
                "sms_budget_exceeded",
                "Tenant daily SMS budget exceeded.",
            )

        existing = session.scalar(
            select(SmsClaim).where(
                SmsClaim.tenant_id == principal.tenant_id,
                SmsClaim.employee_id == employee_id,
                SmsClaim.period == period,
                SmsClaim.local_date == local_day,
            )
        )
        if existing is not None:
            return ToolResult.success(_claim_payload(existing, replayed=True))

        claim = SmsClaim(
            tenant_id=principal.tenant_id,
            employee_id=employee_id,
            period=period,
            local_date=local_day,
            template_id=CALLER_PROVIDED_TEMPLATE_ID,
            state="pending",
            provider=config.settings.sms.provider,
            provider_message_id=None,
            telephone_hash=telephone_hash(e164),
            created_at=now,
            updated_at=now,
        )
        session.add(claim)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            winner = session.scalar(
                select(SmsClaim).where(
                    SmsClaim.tenant_id == principal.tenant_id,
                    SmsClaim.employee_id == employee_id,
                    SmsClaim.period == period,
                    SmsClaim.local_date == local_day,
                )
            )
            assert winner is not None
            return ToolResult.success(_claim_payload(winner, replayed=True))

        claim_id = claim.id

    destination_last4 = "".join(ch for ch in e164 if ch.isdigit())[-4:]
    # Provider call outside the insert transaction; claim already durable as pending.
    result = gateway.send(
        SmsSendRequest(
            to_e164=e164,
            body=body,
            sender=config.settings.sms.elks_from,
        )
    )

    with session_factory() as session:
        claim = session.get(SmsClaim, claim_id)
        assert claim is not None
        if claim.state != "pending":
            # Another path already finalized; treat as replay.
            return ToolResult.success(
                _claim_payload(
                    claim,
                    replayed=True,
                    destination_last4=destination_last4,
                )
            )
        claim.state = result.outcome
        claim.provider_message_id = result.provider_message_id
        claim.updated_at = datetime.now(UTC)
        session.commit()
        return ToolResult.success(
            _claim_payload(
                claim,
                replayed=False,
                provider_detail=result.detail,
                destination_last4=destination_last4,
            )
        )
