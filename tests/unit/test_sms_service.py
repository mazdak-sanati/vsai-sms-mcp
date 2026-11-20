"""M6 acceptance: mock SMS, claims, suppression, budget."""

from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select

from tests.fixtures.workbook_factory import october_full_rows, write_timesheet
from timereport_mcp.config import load_config
from timereport_mcp.observability.logging import RedactingFilter, configure_logging
from timereport_mcp.persistence.models import SmsClaim
from timereport_mcp.runtime import build_runtime
from timereport_mcp.sms.gateway import MockSmsGateway
from timereport_mcp.sms.service import get_sms_status, send_sms, set_sms_suppression

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config" / "tenants.example.toml"
TOKEN_A = "tenant-a-token-synthetic"
FIXTURE = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
CLOCK = date(2026, 10, 15)


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
    write_timesheet(FIXTURE, october_full_rows())
    config = load_config(EXAMPLE_CONFIG, environ=_env(db_name))
    gateway = MockSmsGateway()
    runtime = build_runtime(config, gateway=gateway)
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    return config, runtime, gateway, principal


def test_first_send_records_one_delivery() -> None:
    config, runtime, gateway, principal = _setup("test-m6-first")
    result = send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data["state"] == "sent"
    assert result.data["replayed"] is False
    assert len(gateway.sent) == 1


def test_repeat_and_concurrent_do_not_double_send() -> None:
    config, runtime, gateway, principal = _setup("test-m6-repeat")
    first = send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert first.ok and first.data and first.data["state"] == "sent"
    second = send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert second.ok and second.data and second.data["replayed"] is True
    assert len(gateway.sent) == 1

    # Concurrent after reset of gateway counter but same DB — use fresh gateway sharing DB.
    gateway2 = MockSmsGateway()

    def attempt():
        return send_sms(
            config,
            runtime.session_factory,
            gateway2,
            principal,
            workbook_id="tenant-a-timesheet",
            employee_id="synthetic-employee-a1",
            period="2026-10",
            template_id="timesheet_reminder",
            clock=CLOCK,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: attempt(), range(4)))
    assert all(r.ok and r.data and r.data["replayed"] for r in results)
    assert len(gateway2.sent) == 0


def test_new_process_same_db_no_second_delivery(tmp_path: Path) -> None:
    write_timesheet(FIXTURE, october_full_rows())
    env = _env("test-m6-persist")
    config = load_config(EXAMPLE_CONFIG, environ=env)
    gateway = MockSmsGateway()
    runtime = build_runtime(config, gateway=gateway)
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert len(gateway.sent) == 1

    # New runtime / gateway, same DATABASE_URL
    gateway2 = MockSmsGateway()
    runtime2 = build_runtime(config, gateway=gateway2)
    result = send_sms(
        config,
        runtime2.session_factory,
        gateway2,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
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
                template_id="timesheet_reminder",
                state="pending",
                provider="mock",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
    result = send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert result.data is not None
    assert result.data["state"] == "pending"
    assert result.data["replayed"] is True
    assert len(gateway.sent) == 0


def test_timeout_and_rejected_paths() -> None:
    write_timesheet(FIXTURE, october_full_rows())
    config = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-fail"))
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None

    timeout_gw = MockSmsGateway(failure_mode="timeout")
    rt = build_runtime(config, gateway=timeout_gw)
    r1 = send_sms(
        config,
        rt.session_factory,
        timeout_gw,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert r1.data is not None
    assert r1.data["state"] == "uncertain"
    r1b = send_sms(
        config,
        rt.session_factory,
        timeout_gw,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert r1b.data is not None
    assert r1b.data["state"] == "uncertain"
    assert r1b.data["replayed"] is True

    # Separate day/employee for rejected path — use different clock day.
    reject_gw = MockSmsGateway(failure_mode="http_400")
    day2 = date(2026, 10, 16)
    r2 = send_sms(
        config,
        rt.session_factory,
        reject_gw,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=day2,
    )
    assert r2.data is not None
    assert r2.data["state"] == "rejected"
    r2b = send_sms(
        config,
        rt.session_factory,
        reject_gw,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=day2,
    )
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
    blocked = send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
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

    # Admin suppression on another employee id path — use same employee after wiping via new db.
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
    blocked2 = send_sms(
        config2,
        rt2.session_factory,
        gw2,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
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
    ok = send_sms(
        config2,
        rt2.session_factory,
        gw2,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert ok.ok is True
    assert len(gw2.sent) == 1


def test_budget_cap_and_bad_phone_and_second_template() -> None:
    write_timesheet(FIXTURE, october_full_rows())
    # Tiny budget via temporary config mutation after load.
    config = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-budget"))
    # Replace tenant max sends
    from dataclasses import replace

    tenant = config.tenants["tenant-a"]
    config.tenants["tenant-a"] = replace(tenant, max_sends_per_tenant_per_local_day=1)
    gateway = MockSmsGateway()
    runtime = build_runtime(config, gateway=gateway)
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None

    # Need a second employee in the workbook for budget test.
    rows = october_full_rows()
    rows.extend(
        october_full_rows(
            employee_id="synthetic-employee-a2",
            name="Synthetic Person A2",
            phone="+46700000002",
        )
    )
    write_timesheet(FIXTURE, rows)

    first = send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert first.ok is True
    second = send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a2",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
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

    # Bad phone
    write_timesheet(
        FIXTURE,
        [
            {
                "employee_id": "bad-phone",
                "employee_name": "Bad Phone",
                "telephone": "not-a-number",
                "date": date(2026, 10, 1),
                "hours": 8,
            }
        ],
    )
    config3 = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-phone"))
    gw3 = MockSmsGateway()
    rt3 = build_runtime(config3, gateway=gw3)
    bad = send_sms(
        config3,
        rt3.session_factory,
        gw3,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="bad-phone",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    assert bad.ok is False
    assert bad.error is not None
    assert bad.error.code == "sms_not_eligible"

    # Second template same day replays first claim
    write_timesheet(FIXTURE, october_full_rows())
    config4 = load_config(EXAMPLE_CONFIG, environ=_env("test-m6-tmpl"))
    # Add a second template by replace
    from timereport_mcp.config import SmsTemplate

    t = config4.tenants["tenant-a"]
    config4.tenants["tenant-a"] = replace(
        t,
        templates=t.templates
        + (SmsTemplate(template_id="other", body="Other reminder for {period}."),),
    )
    gw4 = MockSmsGateway()
    rt4 = build_runtime(config4, gateway=gw4)
    a = send_sms(
        config4,
        rt4.session_factory,
        gw4,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
    b = send_sms(
        config4,
        rt4.session_factory,
        gw4,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="other",
        clock=CLOCK,
    )
    assert a.data is not None and b.data is not None
    assert b.data["replayed"] is True
    assert b.data["template_id"] == "timesheet_reminder"
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
    send_sms(
        config,
        runtime.session_factory,
        gateway,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        period="2026-10",
        template_id="timesheet_reminder",
        clock=CLOCK,
    )
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
