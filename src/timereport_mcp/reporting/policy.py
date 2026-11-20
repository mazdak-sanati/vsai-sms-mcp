"""Deterministic expected vs reported hours calculation."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from timereport_mcp.config import (
    AppConfig,
    EmployeePolicy,
    HolidayCalendar,
    TenantConfig,
    WeekdayHours,
)
from timereport_mcp.excel.reader import TimesheetEntry, TimesheetRead

FULL_TIME_WEEKDAY = WeekdayHours(
    monday=8.0,
    tuesday=8.0,
    wednesday=8.0,
    thursday=8.0,
    friday=8.0,
    saturday=0.0,
    sunday=0.0,
)

Status = Literal[
    "complete",
    "incomplete",
    "policy_review_required",
    "data_review_required",
]


@dataclass(slots=True)
class EmployeeStatus:
    employee_id: str
    employee_name: str
    expected_hours: float | None
    reported_hours: float
    status: Status
    policy_warnings: list[str]


def period_bounds(period: str) -> tuple[date, date]:
    year_s, month_s = period.split("-", 1)
    year, month = int(year_s), int(month_s)
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    return start, end


def _iter_days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current = date.fromordinal(current.toordinal() + 1)


def _holiday_dates(
    tenant: TenantConfig,
    calendars: dict[str, HolidayCalendar],
    start: date,
    end: date,
) -> tuple[frozenset[date] | None, list[str]]:
    """Return holiday set for the period, or None if policy incomplete."""
    warnings: list[str] = []
    if tenant.holiday is None:
        warnings.append("holiday_policy_missing")
        return None, warnings
    if tenant.holiday.mode == "none_confirmed":
        assert tenant.holiday.range_start is not None
        assert tenant.holiday.range_end is not None
        if start < tenant.holiday.range_start or end > tenant.holiday.range_end:
            warnings.append("holiday_none_confirmed_out_of_range")
            return None, warnings
        return frozenset(), warnings
    # calendar mode
    cal_id = tenant.holiday.calendar_id
    assert cal_id is not None
    cal = calendars.get(cal_id)
    if cal is None:
        warnings.append("holiday_calendar_missing")
        return None, warnings
    # Require that every day of the month is "covered" by the calendar's existence;
    # synthetic calendars list only holiday dates. Coverage is treated as complete
    # when the calendar is referenced; missing dates are ordinary workdays.
    return frozenset(d for d in cal.dates if start <= d <= end), warnings


def _leave_days(
    tenant: TenantConfig,
    employee_id: str,
    start: date,
    end: date,
) -> frozenset[date]:
    days: set[date] = set()
    for record in tenant.leave:
        if record.employee_id != employee_id:
            continue
        for day in _iter_days(max(record.start, start), min(record.end, end)):
            days.add(day)
    return frozenset(days)


def _resolve_weekday_hours(
    tenant: TenantConfig,
    policy: EmployeePolicy | None,
) -> tuple[WeekdayHours | None, list[str]]:
    warnings: list[str] = []
    if policy is None:
        warnings.append("employee_policy_missing")
        return None, warnings
    if policy.classification == "full_time":
        if tenant.full_time_profile != "se_full_time_40":
            warnings.append("unknown_full_time_profile")
            return None, warnings
        return FULL_TIME_WEEKDAY, warnings
    # part_time
    if policy.weekday_hours is None:
        warnings.append("part_time_weekday_map_missing")
        return None, warnings
    return policy.weekday_hours, warnings


def expected_hours_for_employee(
    tenant: TenantConfig,
    calendars: dict[str, HolidayCalendar],
    employee_id: str,
    period: str,
) -> tuple[float | None, list[str]]:
    start, end = period_bounds(period)
    policy_map = {e.employee_id: e for e in tenant.employees}
    policy = policy_map.get(employee_id)
    weekday_hours, warnings = _resolve_weekday_hours(tenant, policy)
    holidays, holiday_warnings = _holiday_dates(tenant, calendars, start, end)
    warnings.extend(holiday_warnings)
    if weekday_hours is None or holidays is None:
        return None, warnings

    leave = _leave_days(tenant, employee_id, start, end)
    total = 0.0
    for day in _iter_days(start, end):
        if day in holidays or day in leave:
            continue
        total += weekday_hours.for_weekday(day.weekday())
    return total, warnings


def compute_reporting_status(
    config: AppConfig,
    tenant_id: str,
    read: TimesheetRead,
    period: str,
    *,
    employee_id: str | None = None,
) -> list[EmployeeStatus]:
    tenant = config.tenants[tenant_id]
    # Group entries by employee.
    by_employee: dict[str, list[TimesheetEntry]] = {}
    for entry in read.entries:
        by_employee.setdefault(entry.employee_id, []).append(entry)

    # Include configured employees even if they have no rows.
    employee_ids = set(by_employee)
    for emp in tenant.employees:
        employee_ids.add(emp.employee_id)
    if employee_id is not None:
        # Always evaluate the requested employee, even with zero rows.
        employee_ids = {employee_id}

    # Map issues to employees via row numbers when possible.
    issue_rows = {i.row_number for i in read.issues}
    results: list[EmployeeStatus] = []
    for emp_id in sorted(employee_ids):
        entries = by_employee.get(emp_id, [])
        name = entries[0].employee_name if entries else emp_id
        has_row_issues = any(e.row_number in issue_rows for e in entries)
        # Also treat duplicate/invalid issues that mention this employee.
        has_named_issues = any(emp_id in i.message for i in read.issues)
        hours_invalid = any(e.hours is None and not e.hours_missing for e in entries)
        # Blank hours count toward reported as 0 but flag data review if we want?
        # Plan: blank hours on an existing row are hours_missing — contribute 0 to sum.
        # Invalid (boolean/formula) already recorded as issues; those rows may still be in entries with hours=None.
        data_bad = has_row_issues or has_named_issues or hours_invalid
        # Duplicate keys: both rows are in entries; issues mention Duplicate.
        if any("Duplicate" in i.message and emp_id in i.message for i in read.issues):
            data_bad = True

        reported = sum(e.hours or 0.0 for e in entries if e.hours is not None)
        # hours_missing rows contribute 0 already.

        expected, warnings = expected_hours_for_employee(
            tenant, config.calendars, emp_id, period
        )

        policy_warnings = list(warnings)
        if data_bad:
            status: Status = "data_review_required"
        elif expected is None:
            status = "policy_review_required"
        elif reported >= expected:
            status = "complete"
            if reported > expected:
                policy_warnings.append("hours_above_expected")
        else:
            status = "incomplete"

        results.append(
            EmployeeStatus(
                employee_id=emp_id,
                employee_name=name,
                expected_hours=expected if status != "policy_review_required" else None,
                reported_hours=reported,
                status=status,
                policy_warnings=policy_warnings,
            )
        )
    return results


def statuses_payload(statuses: list[EmployeeStatus]) -> list[dict[str, Any]]:
    return [
        {
            "employee_id": s.employee_id,
            "employee_name": s.employee_name,
            "expected_hours": s.expected_hours,
            "reported_hours": s.reported_hours,
            "status": s.status,
            "policy_warnings": s.policy_warnings,
        }
        for s in statuses
    ]
