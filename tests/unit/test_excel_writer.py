"""M5 acceptance: controlled workbook updates."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from tests.fixtures.workbook_factory import october_full_rows, write_timesheet
from timereport_mcp.config import load_config
from timereport_mcp.excel.reader import file_version
from timereport_mcp.excel.writer import update_timesheet_entry
from timereport_mcp.persistence.audit import FileAuditLog

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config" / "tenants.example.toml"
TOKEN_A = "tenant-a-token-synthetic"
TOKEN_B = "tenant-b-token-synthetic"
FIXTURE = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "SMS_PROVIDER": "mock",
        "SMS_LIVE_SEND": "false",
        "TIMEREPORT_DATA_DIR": str(ROOT / "data" / "test-m5"),
    }
    env.update(overrides)
    return env


def _setup_workbook() -> None:
    write_timesheet(
        FIXTURE,
        october_full_rows(),
        extra_sheet={"name": "Notes", "headers": ["note"], "rows": [["preserve-me"]]},
    )


def test_version_match_writes_and_preserves_other_sheet() -> None:
    _setup_workbook()
    config = load_config(EXAMPLE_CONFIG, environ=_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    version = file_version(FIXTURE)
    result = update_timesheet_entry(
        config,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        entry_date=date(2026, 10, 1),
        hours=7.5,
        expected_version=version,
    )
    assert result.ok is True
    assert result.data is not None
    assert result.data["version"] != version

    wb = load_workbook(FIXTURE)
    assert wb["Notes"]["A2"].value == "preserve-me"
    ws = wb["Timesheet"]
    # Find Oct 1 row
    found = None
    for row in ws.iter_rows(min_row=2):
        if row[3].value == date(2026, 10, 1) or (
            hasattr(row[3].value, "date") and row[3].value.date() == date(2026, 10, 1)
        ):
            found = row[4].value
            break
    wb.close()
    assert found == 7.5


def test_stale_version_does_not_change_bytes() -> None:
    _setup_workbook()
    before = FIXTURE.read_bytes()
    config = load_config(EXAMPLE_CONFIG, environ=_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    result = update_timesheet_entry(
        config,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        entry_date=date(2026, 10, 1),
        hours=1.0,
        expected_version="0" * 64,
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "version_conflict"
    assert FIXTURE.read_bytes() == before


def test_concurrent_writers_one_success() -> None:
    _setup_workbook()
    config = load_config(EXAMPLE_CONFIG, environ=_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    version = file_version(FIXTURE)

    def attempt(hours: float):
        return update_timesheet_entry(
            config,
            principal,
            workbook_id="tenant-a-timesheet",
            employee_id="synthetic-employee-a1",
            entry_date=date(2026, 10, 2),
            hours=hours,
            expected_version=version,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [3.0, 4.0]))
    codes = []
    for r in results:
        if r.ok:
            codes.append("ok")
        else:
            assert r.error is not None
            codes.append(r.error.code)
    assert codes.count("ok") == 1
    assert codes.count("version_conflict") == 1


def test_lock_timeout_observable() -> None:
    _setup_workbook()
    config = load_config(EXAMPLE_CONFIG, environ=_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    from filelock import FileLock

    from timereport_mcp.excel.writer import _lock_path

    lock = FileLock(
        str(_lock_path(config.settings.data_dir, principal.tenant_id, "tenant-a-timesheet"))
    )
    lock.acquire()
    try:
        result = update_timesheet_entry(
            config,
            principal,
            workbook_id="tenant-a-timesheet",
            employee_id="synthetic-employee-a1",
            entry_date=date(2026, 10, 1),
            hours=8.0,
            expected_version=file_version(FIXTURE),
            lock_timeout=0.1,
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "lock_timeout"
    finally:
        lock.release()


def test_formula_target_refused() -> None:
    write_timesheet(
        FIXTURE,
        [
            {
                "employee_id": "synthetic-employee-a1",
                "employee_name": "Synthetic Person A",
                "telephone": "+46700000001",
                "date": date(2026, 10, 1),
                "hours": "=1+1",
            }
        ],
    )
    config = load_config(EXAMPLE_CONFIG, environ=_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    result = update_timesheet_entry(
        config,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        entry_date=date(2026, 10, 1),
        hours=8.0,
        expected_version=file_version(FIXTURE),
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "formula_not_supported"


def test_create_if_missing_false_does_not_add_row() -> None:
    _setup_workbook()
    config = load_config(EXAMPLE_CONFIG, environ=_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    before_rows = load_workbook(FIXTURE)["Timesheet"].max_row
    result = update_timesheet_entry(
        config,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        entry_date=date(2026, 11, 2),  # not in October sheet
        hours=8.0,
        expected_version=file_version(FIXTURE),
        create_if_missing=False,
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "not_found"
    after_rows = load_workbook(FIXTURE)["Timesheet"].max_row
    assert after_rows == before_rows


def test_backup_equals_preimage() -> None:
    _setup_workbook()
    preimage = FIXTURE.read_bytes()
    config = load_config(EXAMPLE_CONFIG, environ=_env())
    principal = config.clients.resolve_token(TOKEN_A)
    assert principal is not None
    version = file_version(FIXTURE)
    result = update_timesheet_entry(
        config,
        principal,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        entry_date=date(2026, 10, 1),
        hours=6.0,
        expected_version=version,
    )
    assert result.ok is True
    backup = (
        config.settings.data_dir
        / "backups"
        / principal.tenant_id
        / "tenant-a-timesheet"
        / f"{version}.xlsx"
    )
    assert backup.read_bytes() == preimage


def test_mutated_audit_fails_hash_chain() -> None:
    data_dir = ROOT / "data" / "test-m5-audit"
    audit_path = data_dir / "audit" / "tenant-a.jsonl"
    if audit_path.exists():
        audit_path.unlink()
    log = FileAuditLog(audit_path)
    log.append({"type": "timesheet_update", "employee_id": "a", "new_hours": 1})
    log.append({"type": "timesheet_update", "employee_id": "a", "new_hours": 2})
    assert log.verify_chain() is True
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(lines[0])
    raw["payload"]["new_hours"] = 99
    lines[0] = json.dumps(raw)
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert FileAuditLog(audit_path).verify_chain() is False


def test_cross_tenant_write_not_found() -> None:
    _setup_workbook()
    config = load_config(EXAMPLE_CONFIG, environ=_env())
    principal_b = config.clients.resolve_token(TOKEN_B)
    assert principal_b is not None
    result = update_timesheet_entry(
        config,
        principal_b,
        workbook_id="tenant-a-timesheet",
        employee_id="synthetic-employee-a1",
        entry_date=date(2026, 10, 1),
        hours=8.0,
        expected_version=file_version(FIXTURE),
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "not_found"
