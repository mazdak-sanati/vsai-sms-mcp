"""M3 acceptance: Excel read and validation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tests.fixtures.workbook_factory import october_full_rows, write_timesheet
from timereport_mcp.config import load_config
from timereport_mcp.excel.reader import read_timesheet
from timereport_mcp.excel.service import inspect_timesheet, read_timesheet_entries

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = ROOT / "config" / "tenants.example.toml"
TOKEN_A = "tenant-a-token-synthetic"
TOKEN_B = "tenant-b-token-synthetic"


def _env() -> dict[str, str]:
    return {
        "SMS_PROVIDER": "mock",
        "SMS_LIVE_SEND": "false",
        "TIMEREPORT_DATA_DIR": str(ROOT / "data" / "test"),
    }


def _config():
    return load_config(EXAMPLE_CONFIG, environ=_env())


def _principal_a(config):
    p = config.clients.resolve_token(TOKEN_A)
    assert p is not None
    return p


def test_canonical_sheet_reads_entries(tmp_path: Path) -> None:
    # Point tenant-a workbook at a generated file under the allowlisted root.
    fixture_path = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
    write_timesheet(
        fixture_path,
        october_full_rows(),
        extra_sheet={"name": "Notes", "headers": ["note"], "rows": [["do-not-touch"]]},
    )
    config = _config()
    principal = _principal_a(config)
    result = inspect_timesheet(config, principal, "tenant-a-timesheet")
    assert result.ok is True
    assert result.data is not None
    assert result.data["schema_ok"] is True
    assert result.data["row_count"] == 22

    entries = read_timesheet_entries(config, principal, "tenant-a-timesheet", "2026-10")
    assert entries.ok is True
    assert entries.data is not None
    assert len(entries.data["entries"]) == 22
    # Second sheet is ignored by reporting columns.
    wb_cfg = config.workbook_for("tenant-a", "tenant-a-timesheet")
    assert wb_cfg is not None
    read = read_timesheet(wb_cfg)
    names_in_entries = {e.employee_name for e in read.entries}
    assert "do-not-touch" not in names_in_entries


def test_missing_column(tmp_path: Path) -> None:
    path = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
    write_timesheet(
        path,
        [{"employee_name": "X", "telephone": "+46700000001", "date": date(2026, 10, 1), "hours": 8}],
        headers=["employee_name", "telephone", "date", "hours"],  # no employee_id — ok for tenant-a? config requires employee_id column
    )
    # tenant-a config requires employee_id column mapped — rewrite without hours instead.
    write_timesheet(
        path,
        [{"employee_id": "a", "employee_name": "X", "telephone": "+46700000001", "date": date(2026, 10, 1)}],
        headers=["employee_id", "employee_name", "telephone", "date"],
    )
    config = _config()
    principal = _principal_a(config)
    result = inspect_timesheet(config, principal, "tenant-a-timesheet")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "schema_mismatch"


def test_ambiguous_date_and_blank_boolean_formula(tmp_path: Path) -> None:
    path = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
    write_timesheet(
        path,
        [
            {
                "employee_id": "a",
                "employee_name": "Synthetic Person A",
                "telephone": "+46700000001",
                "date": "01/02/2026",
                "hours": 8,
            },
            {
                "employee_id": "a",
                "employee_name": "Synthetic Person A",
                "telephone": "+46700000001",
                "date": date(2026, 10, 2),
                "hours": None,
            },
            {
                "employee_id": "a",
                "employee_name": "Synthetic Person A",
                "telephone": "+46700000001",
                "date": date(2026, 10, 3),
                "hours": True,
            },
            {
                "employee_id": "a",
                "employee_name": "Synthetic Person A",
                "telephone": "+46700000001",
                "date": date(2026, 10, 6),
                "hours": "=A1+1",
            },
        ],
    )
    config = _config()
    wb = config.workbook_for("tenant-a", "tenant-a-timesheet")
    assert wb is not None
    read = read_timesheet(wb, period="2026-10")
    codes = {i.code for i in read.issues}
    assert "validation_error" in codes
    assert "formula_not_supported" in codes
    # blank hours row becomes an entry with hours_missing
    missing = [e for e in read.entries if e.hours_missing]
    assert missing


def test_duplicate_keys(tmp_path: Path) -> None:
    path = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
    day = date(2026, 10, 1)
    write_timesheet(
        path,
        [
            {
                "employee_id": "a",
                "employee_name": "Synthetic Person A",
                "telephone": "+46700000001",
                "date": day,
                "hours": 8,
            },
            {
                "employee_id": "a",
                "employee_name": "Synthetic Person A",
                "telephone": "+46700000001",
                "date": day,
                "hours": 4,
            },
        ],
    )
    config = _config()
    wb = config.workbook_for("tenant-a", "tenant-a-timesheet")
    assert wb is not None
    read = read_timesheet(wb)
    assert any("Duplicate" in i.message for i in read.issues)


def test_corrupt_zip_bytes() -> None:
    path = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
    path.write_bytes(b"not-an-xlsx")
    config = _config()
    principal = _principal_a(config)
    result = inspect_timesheet(config, principal, "tenant-a-timesheet")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "workbook_unreadable"


def test_cross_tenant_workbook_not_found() -> None:
    path = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
    write_timesheet(path, october_full_rows())
    config = _config()
    principal_b = config.clients.resolve_token(TOKEN_B)
    assert principal_b is not None
    result = inspect_timesheet(config, principal_b, "tenant-a-timesheet")
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "not_found"


def test_row_limit_injected() -> None:
    path = ROOT / "tests" / "fixtures" / "tenant-a" / "timesheet.xlsx"
    write_timesheet(path, october_full_rows())
    config = _config()
    principal = _principal_a(config)
    result = inspect_timesheet(config, principal, "tenant-a-timesheet", max_rows=5)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "workbook_unreadable"


def test_path_argument_not_accepted_by_service() -> None:
    # Service API has no path parameter — only workbook_id.
    import inspect

    sig = inspect.signature(inspect_timesheet)
    assert "path" not in sig.parameters
    assert "workbook_id" in sig.parameters
