"""M6 acceptance: mock SMS, claims, suppression, budget."""

from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select

from timereport_mcp.config import load_config
from timereport_mcp.observability.logging import RedactingFilter, configure_logging
from timereport_mcp.persistence.models import SmsClaim
from timereport_mcp.runtime import build_runtime
from timereport_mcp.sms.gateway import MockSmsGateway
from timereport_mcp.sms.service import get_sms_status, send_sms, set_sms_suppression

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config" / "tenants.example.toml"
TOKEN_A = "tenant-a-token-synthetic"
CLOCK = date(2026, 10, 15)
PHONE_A1 = "+46700000001"
PHONE_A2 = "+46700000002"
MSG = "Hej! Det saknas 8 timmar i din tidrapport för 2026-10. /tenant-a"


def _env(db_name: str) -> dict[str, str]:
    data = ROOT / "data" / db_name
    data.mkdir(parents=True, exist_ok=True)
    db_path = data / "sms.db"
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix) if suffix else db_path
        if candidate.exists():
            candidate.unlink()
    return {
        "SMS_PROVIDER": "mock",
        "SMS_LIVE_SEND": "false",
        "TIMEREPORT_DATA_DIR": str(data),
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
    }


def _setup(db_name: str):
    config = load_config(EXAMPLE_CONFIG, environ=_env(db_name))
    gateway = MockSmsGateway()
    runtime = build_runtime(config, gateway=gateway)
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    return config, runtime, gateway, principal


def _send(
    config,
    runtime,
    gateway,
    principal,
    *,
    employee_id: str = "synthetic-employee-a1",
    to: str = PHONE_A1,
    message: str = MSG,
    period: str = "2026-10",
    clock: date = CLOCK,
):
    return send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        to=to,
        message=message,
        employee_id=employee_id,
        period=period,
        clock=clock,
    )


def test_first_send_records_one_delivery() -> None:
    config, runtime, gateway, principal = _setup("test-m6-first")
    result = _send(config, runtime, gateway, principal)
    assert result.ok is True
    assert result.data is not None
    assert result.data["state"] == "sent"
    assert result.data["replayed"] is False
    assert result.data["destination_last4"] == "0001"
    assert len(gateway.sent) == 1
    assert gateway.sent[0].to_e164 == PHONE_A1
    assert gateway.sent[0].body == MSG


def test_repeat_and_concurrent_do_not_double_send() -> None:
    config, runtime, gateway, principal = _setup("test-m6-repeat")
    first = _send(config, runtime, gateway, principal)
    assert first.ok and first.data and first.data["state"] == "sent"
    second = _send(config, runtime, gateway, principal)
    assert second.ok and second.data and second.data["replayed"] is True
    assert len(gateway.sent) == 1

    gateway2 = MockSmsGateway()

    def attempt():
        return _send(config, runtime, gateway2, principal)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: attempt(), range(4)))
    assert all(r.ok and r.data and r.data["replayed"] for r in results)
    assert len(gateway2.sent) == 0


def test_new_process_same_db_no_second_delivery() -> None:
    env = _env("test-m6-persist")
    config = load_config(EXAMPLE_CONFIG, environ=env)
    gateway = MockSmsGateway()
    runtime = build_runtime(config, gateway=gateway)
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    _send(config, runtime, gateway, principal)
    assert len(gateway.sent) == 1

    gateway2 = MockSmsGateway()
    runtime2 = build_runtime(config, gateway=gateway2)
    result = _send(config, runtime2, gateway2, principal)
    assert result.data is not None
    assert result.data["replayed"] is True
    assert len(gateway2.sent) == 0


def test_pending_row_does_not_call_provider() -> None:
    config, runtime, gateway, principal = _setup("test-m6-pending")
    with runtime.session_factory() as session:
        session.add(
            SmsClaim(
                tenant_id=principal.tenant_id,
                employee_id="synthetic-employee-a1",
                period="2026-10",
                local_date=CLOCK,
                template_id="caller_provided",
                state="pending",
                provider="mock",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
    result = _send(config, runtime, gateway, principal)
    assert result.data is not None
    assert result.data["state"] == "pending"
    assert result.data["replayed"] is True
    assert len(gateway.sent) == 0


def test_timeout_and_rejected_paths() -> None:
    config = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-fail"))
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None

    timeout_gw = MockSmsGateway(failure_mode="timeout")
    rt = build_runtime(config, gateway=timeout_gw)
    r1 = _send(config, rt, timeout_gw, principal)
    assert r1.data is not None
    assert r1.data["state"] == "uncertain"
    r1b = _send(config, rt, timeout_gw, principal)
    assert r1b.data is not None
    assert r1b.data["state"] == "uncertain"
    assert r1b.data["replayed"] is True

    reject_gw = MockSmsGateway(failure_mode="http_400")
    day2 = date(2026, 10, 16)
    r2 = _send(config, rt, reject_gw, principal, clock=day2)
    assert r2.data is not None
    assert r2.data["state"] == "rejected"
    r2b = _send(config, rt, reject_gw, principal, clock=day2)
    assert r2b.data is not None
    assert r2b.data["state"] == "rejected"
    assert r2b.data["replayed"] is True


def test_suppression_opt_out_and_admin() -> None:
    config, runtime, gateway, principal = _setup("test-m6-suppress")
    set_sms_suppression(
        config,
        runtime.session_factory,
        principal,
        employee_id="synthetic-employee-a1",
        reason="opt_out",
        active=True,
    )
    blocked = _send(config, runtime, gateway, principal)
    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code == "sms_suppressed"
    assert len(gateway.sent) == 0

    clear = set_sms_suppression(
        config,
        runtime.session_factory,
        principal,
        employee_id="synthetic-employee-a1",
        reason="opt_out",
        active=False,
    )
    assert clear.ok is False

    config2 = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-admin"))
    gw2 = MockSmsGateway()
    rt2 = build_runtime(config2, gateway=gw2)
    set_sms_suppression(
        config2,
        rt2.session_factory,
        principal,
        employee_id="synthetic-employee-a1",
        reason="admin",
        active=True,
    )
    blocked2 = _send(config2, rt2, gw2, principal)
    assert blocked2.error is not None
    assert blocked2.error.code == "sms_suppressed"
    set_sms_suppression(
        config2,
        rt2.session_factory,
        principal,
        employee_id="synthetic-employee-a1",
        reason="admin",
        active=False,
    )
    ok = _send(config2, rt2, gw2, principal)
    assert ok.ok is True
    assert len(gw2.sent) == 1


def test_budget_cap_and_bad_phone_and_message_validation() -> None:
    from dataclasses import replace

    config = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-budget"))
    tenant = config.tenants["tenant-a"]
    config.tenants["tenant-a"] = replace(tenant, max_sends_per_tenant_per_local_day=1)
    gateway = MockSmsGateway()
    runtime = build_runtime(config, gateway=gateway)
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None

    first = _send(config, runtime, gateway, principal)
    assert first.ok is True
    second = _send(
        config,
        runtime,
        gateway,
        principal,
        employee_id="synthetic-employee-a2",
        to=PHONE_A2,
    )
    assert second.ok is False
    assert second.error is not None
    assert second.error.code == "sms_budget_exceeded"
    with runtime.session_factory() as session:
        count = len(
            session.scalars(
                select(SmsClaim).where(SmsClaim.employee_id == "synthetic-employee-a2")
            ).all()
        )
        assert count == 0

    config3 = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-phone"))
    gw3 = MockSmsGateway()
    rt3 = build_runtime(config3, gateway=gw3)
    bad = _send(
        config3,
        rt3,
        gw3,
        principal,
        employee_id="bad-phone",
        to="not-a-number",
    )
    assert bad.ok is False
    assert bad.error is not None
    assert bad.error.code == "sms_not_eligible"

    empty = _send(
        config3,
        rt3,
        gw3,
        principal,
        employee_id="empty-msg",
        message="   ",
        clock=date(2026, 10, 17),
    )
    assert empty.ok is False
    assert empty.error is not None
    assert empty.error.code == "validation_error"

    # Same employee/period/day with a different message still replays first claim.
    config4 = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-msg-replay"))
    gw4 = MockSmsGateway()
    rt4 = build_runtime(config4, gateway=gw4)
    a = _send(config4, rt4, gw4, principal, message=MSG)
    b = _send(config4, rt4, gw4, principal, message="Other reminder body.")
    assert a.data is not None and b.data is not None
    assert b.data["replayed"] is True
    assert len(gw4.sent) == 1


def test_logs_redact_phone_body_and_token() -> None:
    configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    log = logging.getLogger("timereport_mcp.sms.test")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("send to +46700000001 bearer tenant-a-token-synthetic message=hello body=secret")
    text = stream.getvalue()
    assert "+46700000001" not in text
    assert "tenant-a-token-synthetic" not in text
    log.removeHandler(handler)


def test_default_provider_is_mock() -> None:
    config = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-default"))
    assert config.settings.sms.provider == "mock"


def test_get_sms_status_roundtrip() -> None:
    config, runtime, gateway, principal = _setup("test-m6-status")
    _send(config, runtime, gateway, principal)
    status = get_sms_status(
        config,
        runtime.session_factory,
        principal,
        employee_id="synthetic-employee-a1",
        period="2026-10",
        local_date=CLOCK,
    )
    assert status.ok is True
    assert status.data is not None
    assert status.data["claim"]["state"] == "sent"
