"""M4 acceptance: expected-hours policy and reporting status."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from timereport_mcp.auth.principals import ClientRecord, PrincipalRegistry
from timereport_mcp.config import (
    AppConfig,
    EmployeePolicy,
    HolidayCalendar,
    HolidayPolicy,
    LeaveRecord,
    ServerSettings,
    SmsSettings,
    TenantConfig,
    WeekdayHours,
)
from timereport_mcp.excel.reader import TimesheetEntry, TimesheetRead
from timereport_mcp.reporting.policy import (
    compute_reporting_status,
    expected_hours_for_employee,
)

ROOT = Path(__file__).resolve().parents[2]


def _tenant(
    *,
    holiday: HolidayPolicy | None,
    employees: list[EmployeePolicy] | None = None,
    leave: list[LeaveRecord] | None = None,
) -> TenantConfig:
    return TenantConfig(
        tenant_id="t1",
        label="T1",
        region="SE",
        timezone="Europe/Stockholm",
        workbook_roots=(ROOT / "tests" / "fixtures" / "tenant-a",),
        workbooks=(),
        employees=tuple(employees or [
            EmployeePolicy(employee_id="emp-1", classification="full_time")
        ]),
        leave=tuple(leave or ()),
        holiday=holiday,
        templates=(),
    )


def _app(tenant: TenantConfig, calendars: dict[str, HolidayCalendar] | None = None) -> AppConfig:
    settings = ServerSettings(
        data_dir=ROOT / "data" / "test",
        database_url="sqlite:///:memory:",
        http_host="127.0.0.1",
        http_port=8000,
        resource_server_url="http://127.0.0.1:8000/mcp",
        auth_issuer_url="https://auth.example.invalid",
        stdio_tenant=None,
        sms=SmsSettings(provider="mock", live_send=False),
    )
    clients = PrincipalRegistry(
        [
            ClientRecord(
                client_id="c1",
                tenant_id=tenant.tenant_id,
                token_sha256="a" * 64,
                scopes=frozenset({"workbooks:read"}),
            )
        ]
    )
    return AppConfig(
        tenants={tenant.tenant_id: tenant},
        clients=clients,
        calendars=calendars or {},
        settings=settings,
        config_path=ROOT / "config" / "tenants.example.toml",
    )


def test_october_2026_full_time_176() -> None:
    tenant = _tenant(
        holiday=HolidayPolicy(
            mode="none_confirmed",
            range_start=date(2026, 1, 1),
            range_end=date(2026, 12, 31),
        )
    )
    expected, warnings = expected_hours_for_employee(tenant, {}, "emp-1", "2026-10")
    assert expected == 176.0
    assert warnings == []


def test_february_2026_full_time_160() -> None:
    tenant = _tenant(
        holiday=HolidayPolicy(
            mode="none_confirmed",
            range_start=date(2026, 1, 1),
            range_end=date(2026, 12, 31),
        )
    )
    expected, _ = expected_hours_for_employee(tenant, {}, "emp-1", "2026-02")
    assert expected == 160.0


def test_configured_holiday_subtracts_8() -> None:
    calendars = {
        "cal": HolidayCalendar(calendar_id="cal", dates=frozenset({date(2026, 10, 5)}))
    }
    tenant = _tenant(holiday=HolidayPolicy(mode="calendar", calendar_id="cal"))
    expected, _ = expected_hours_for_employee(tenant, calendars, "emp-1", "2026-10")
    assert expected == 168.0  # 176 - 8; Oct 5 2026 is Monday


def test_none_confirmed_leaves_weekdays() -> None:
    tenant = _tenant(
        holiday=HolidayPolicy(
            mode="none_confirmed",
            range_start=date(2026, 1, 1),
            range_end=date(2026, 12, 31),
        )
    )
    expected, _ = expected_hours_for_employee(tenant, {}, "emp-1", "2026-10")
    assert expected == 176.0


def test_missing_holiday_policy_review() -> None:
    tenant = _tenant(holiday=None)
    expected, warnings = expected_hours_for_employee(tenant, {}, "emp-1", "2026-10")
    assert expected is None
    assert "holiday_policy_missing" in warnings


def test_part_time_weekday_map() -> None:
    tenant = _tenant(
        holiday=HolidayPolicy(
            mode="none_confirmed",
            range_start=date(2026, 1, 1),
            range_end=date(2026, 12, 31),
        ),
        employees=[
            EmployeePolicy(
                employee_id="pt-1",
                classification="part_time",
                weekday_hours=WeekdayHours(
                    monday=4, tuesday=4, wednesday=4, thursday=4, friday=4
                ),
            )
        ],
    )
    expected, _ = expected_hours_for_employee(tenant, {}, "pt-1", "2026-10")
    assert expected == 88.0  # 22 * 4


def test_part_time_without_map_policy_review() -> None:
    tenant = _tenant(
        holiday=HolidayPolicy(
            mode="none_confirmed",
            range_start=date(2026, 1, 1),
            range_end=date(2026, 12, 31),
        ),
        employees=[
            EmployeePolicy(employee_id="pt-1", classification="part_time", weekday_hours=None)
        ],
    )
    expected, warnings = expected_hours_for_employee(tenant, {}, "pt-1", "2026-10")
    assert expected is None
    assert "part_time_weekday_map_missing" in warnings


def test_approved_leave_subtracts_wednesday() -> None:
    wed = date(2026, 10, 7)  # Wednesday
    tenant = _tenant(
        holiday=HolidayPolicy(
            mode="none_confirmed",
            range_start=date(2026, 1, 1),
            range_end=date(2026, 12, 31),
        ),
        leave=[LeaveRecord(employee_id="emp-1", start=wed, end=wed, kind="leave")],
    )
    expected, _ = expected_hours_for_employee(tenant, {}, "emp-1", "2026-10")
    assert expected == 168.0


def test_blank_cell_not_leave_and_hours_above_expected() -> None:
    tenant = _tenant(
        holiday=HolidayPolicy(
            mode="none_confirmed",
            range_start=date(2026, 1, 1),
            range_end=date(2026, 12, 31),
        )
    )
    app = _app(tenant)
    # 22 weekdays * 9 hours = 198 > 176
    entries = []
    day = date(2026, 10, 1)
    while day.month == 10:
        if day.weekday() < 5:
            entries.append(
                TimesheetEntry(
                    employee_id="emp-1",
                    employee_name="Synthetic Person A",
                    telephone="+46700000001",
                    entry_date=day,
                    hours=9.0,
                    row_number=len(entries) + 2,
                    identity_is_name_derived=False,
                )
            )
        day = date.fromordinal(day.toordinal() + 1)
    # One blank-hours row on a Saturday should not invent leave.
    entries.append(
        TimesheetEntry(
            employee_id="emp-1",
            employee_name="Synthetic Person A",
            telephone="+46700000001",
            entry_date=date(2026, 10, 3),
            hours=None,
            row_number=100,
            identity_is_name_derived=False,
            hours_missing=True,
        )
    )
    read = TimesheetRead(workbook_id="wb", version="v", sheet="Timesheet", entries=entries)
    statuses = compute_reporting_status(app, "t1", read, "2026-10", employee_id="emp-1")
    assert len(statuses) == 1
    assert statuses[0].status == "complete"
    assert "hours_above_expected" in statuses[0].policy_warnings
    assert statuses[0].expected_hours == 176.0


def test_invalid_rows_data_review_required() -> None:
    tenant = _tenant(
        holiday=HolidayPolicy(
            mode="none_confirmed",
            range_start=date(2026, 1, 1),
            range_end=date(2026, 12, 31),
        )
    )
    app = _app(tenant)
    from timereport_mcp.excel.reader import RowIssue

    read = TimesheetRead(
        workbook_id="wb",
        version="v",
        sheet="Timesheet",
        entries=[
            TimesheetEntry(
                employee_id="emp-1",
                employee_name="Synthetic Person A",
                telephone="+46700000001",
                entry_date=date(2026, 10, 1),
                hours=None,
                row_number=2,
                identity_is_name_derived=False,
            )
        ],
        issues=[RowIssue(2, "formula_not_supported", "Formula cells are not supported for hours.")],
    )
    statuses = compute_reporting_status(app, "t1", read, "2026-10", employee_id="emp-1")
    assert statuses[0].status == "data_review_required"


def test_no_sms_reply_input_in_status_signature() -> None:
    import inspect

    sig = inspect.signature(compute_reporting_status)
    assert "sms" not in sig.parameters
    assert "reply" not in sig.parameters
