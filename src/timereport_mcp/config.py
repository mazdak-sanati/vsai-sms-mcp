"""Trusted server-side configuration loading."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from timereport_mcp.auth.principals import ALL_SCOPES, ClientRecord, PrincipalRegistry


class ConfigError(ValueError):
    """Raised when configuration is invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    employee_name: str = "employee_name"
    telephone: str = "telephone"
    date: str = "date"
    hours: str = "hours"
    employee_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkbookConfig:
    workbook_id: str
    label: str
    path: Path
    sheet: str = "Timesheet"
    columns: ColumnMapping = field(default_factory=ColumnMapping)


@dataclass(frozen=True, slots=True)
class WeekdayHours:
    monday: float
    tuesday: float
    wednesday: float
    thursday: float
    friday: float
    saturday: float = 0.0
    sunday: float = 0.0

    def for_weekday(self, weekday: int) -> float:
        """weekday: Monday=0 ... Sunday=6."""
        values = (
            self.monday,
            self.tuesday,
            self.wednesday,
            self.thursday,
            self.friday,
            self.saturday,
            self.sunday,
        )
        return values[weekday]


@dataclass(frozen=True, slots=True)
class EmployeePolicy:
    employee_id: str
    classification: Literal["full_time", "part_time"]
    weekday_hours: WeekdayHours | None = None


@dataclass(frozen=True, slots=True)
class LeaveRecord:
    employee_id: str
    start: date
    end: date
    kind: Literal["leave", "sick"]


@dataclass(frozen=True, slots=True)
class HolidayCalendar:
    calendar_id: str
    dates: frozenset[date]


@dataclass(frozen=True, slots=True)
class HolidayPolicy:
    mode: Literal["calendar", "none_confirmed"]
    calendar_id: str | None = None
    range_start: date | None = None
    range_end: date | None = None


@dataclass(frozen=True, slots=True)
class SmsTemplate:
    template_id: str
    body: str


@dataclass(frozen=True, slots=True)
class TenantConfig:
    tenant_id: str
    label: str
    region: str
    timezone: str
    workbook_roots: tuple[Path, ...]
    workbooks: tuple[WorkbookConfig, ...]
    employees: tuple[EmployeePolicy, ...]
    leave: tuple[LeaveRecord, ...]
    holiday: HolidayPolicy | None
    templates: tuple[SmsTemplate, ...]
    max_sends_per_tenant_per_local_day: int = 50
    full_time_profile: str = "se_full_time_40"


@dataclass(frozen=True, slots=True)
class SmsSettings:
    provider: Literal["mock", "elks"]
    live_send: bool
    elks_username: str | None = None
    elks_password: str | None = None
    elks_from: str | None = None


@dataclass(frozen=True, slots=True)
class ServerSettings:
    data_dir: Path
    database_url: str
    http_host: str
    http_port: int
    resource_server_url: str
    auth_issuer_url: str
    stdio_tenant: str | None
    sms: SmsSettings


@dataclass(frozen=True, slots=True)
class AppConfig:
    tenants: dict[str, TenantConfig]
    clients: PrincipalRegistry
    calendars: dict[str, HolidayCalendar]
    settings: ServerSettings
    config_path: Path

    def require_tenant(self, tenant_id: str) -> TenantConfig:
        try:
            return self.tenants[tenant_id]
        except KeyError as exc:
            raise ConfigError(f"Unknown tenant {tenant_id}") from exc

    def workbook_for(self, tenant_id: str, workbook_id: str) -> WorkbookConfig | None:
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
            return None
        for workbook in tenant.workbooks:
            if workbook.workbook_id == workbook_id:
                return workbook
        return None


def _parse_date(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"Invalid date for {field_name}: {value!r}") from exc


def _resolve_under_roots(path: Path, roots: tuple[Path, ...]) -> Path:
    resolved = path.resolve()
    for root in roots:
        root_resolved = root.resolve()
        try:
            resolved.relative_to(root_resolved)
            return resolved
        except ValueError:
            continue
    raise ConfigError(
        f"Workbook path {resolved} is outside configured workbook_roots "
        f"{[str(r) for r in roots]}"
    )


def _load_weekday_hours(raw: dict[str, Any] | None) -> WeekdayHours | None:
    if raw is None:
        return None
    required = ("monday", "tuesday", "wednesday", "thursday", "friday")
    missing = [k for k in required if k not in raw]
    if missing:
        raise ConfigError(f"part_time weekday_hours missing keys: {missing}")
    return WeekdayHours(
        monday=float(raw["monday"]),
        tuesday=float(raw["tuesday"]),
        wednesday=float(raw["wednesday"]),
        thursday=float(raw["thursday"]),
        friday=float(raw["friday"]),
        saturday=float(raw.get("saturday", 0.0)),
        sunday=float(raw.get("sunday", 0.0)),
    )


def _load_calendar_file(path: Path) -> HolidayCalendar:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    calendar_id = str(raw.get("calendar_id") or path.stem)
    dates_raw = raw.get("dates")
    if not isinstance(dates_raw, list):
        raise ConfigError(f"Calendar {path} must contain a dates array")
    dates = frozenset(_parse_date(str(d), field_name=f"{path.name}.dates") for d in dates_raw)
    return HolidayCalendar(calendar_id=calendar_id, dates=dates)


def _load_holiday_policy(raw: dict[str, Any] | None) -> HolidayPolicy | None:
    if raw is None:
        return None
    mode = raw.get("mode")
    if mode == "calendar":
        calendar_id = raw.get("calendar_id")
        if not calendar_id:
            raise ConfigError("holiday.mode=calendar requires calendar_id")
        return HolidayPolicy(mode="calendar", calendar_id=str(calendar_id))
    if mode == "none_confirmed":
        start = raw.get("range_start")
        end = raw.get("range_end")
        if not start or not end:
            raise ConfigError("holiday.mode=none_confirmed requires range_start and range_end")
        return HolidayPolicy(
            mode="none_confirmed",
            range_start=_parse_date(str(start), field_name="holiday.range_start"),
            range_end=_parse_date(str(end), field_name="holiday.range_end"),
        )
    raise ConfigError(f"Unknown holiday.mode: {mode!r}")


def _load_template(raw: dict[str, Any]) -> SmsTemplate:
    template_id = str(raw["template_id"])
    body = str(raw["body"])
    if len(body) > 480:
        raise ConfigError(f"Template {template_id} exceeds 480 characters")
    # Only {period} may be interpolated later; reject other braces patterns lightly.
    if "{" in body:
        allowed = "{period}"
        # Replace allowed once and ensure no braces remain.
        remainder = body.replace(allowed, "")
        if "{" in remainder or "}" in remainder:
            raise ConfigError(
                f"Template {template_id} may only contain the {{period}} placeholder"
            )
    return SmsTemplate(template_id=template_id, body=body)


def _load_tenant(
    raw: dict[str, Any],
    *,
    config_dir: Path,
    calendars: dict[str, HolidayCalendar],
) -> TenantConfig:
    tenant_id = str(raw["tenant_id"])
    roots_raw = raw.get("workbook_roots") or []
    if not roots_raw:
        raise ConfigError(f"Tenant {tenant_id} requires workbook_roots")
    roots = tuple((config_dir / Path(str(r))).resolve() if not Path(str(r)).is_absolute()
                  else Path(str(r)).resolve() for r in roots_raw)

    workbooks: list[WorkbookConfig] = []
    for wb in raw.get("workbooks") or []:
        cols_raw = wb.get("columns") or {}
        columns = ColumnMapping(
            employee_name=str(cols_raw.get("employee_name", "employee_name")),
            telephone=str(cols_raw.get("telephone", "telephone")),
            date=str(cols_raw.get("date", "date")),
            hours=str(cols_raw.get("hours", "hours")),
            employee_id=(
                str(cols_raw["employee_id"]) if cols_raw.get("employee_id") else None
            ),
        )
        path = Path(str(wb["path"]))
        if not path.is_absolute():
            path = (config_dir / path).resolve()
        else:
            path = path.resolve()
        path = _resolve_under_roots(path, roots)
        workbooks.append(
            WorkbookConfig(
                workbook_id=str(wb["workbook_id"]),
                label=str(wb.get("label", wb["workbook_id"])),
                path=path,
                sheet=str(wb.get("sheet", "Timesheet")),
                columns=columns,
            )
        )

    employees: list[EmployeePolicy] = []
    for emp in raw.get("employees") or []:
        classification = str(emp["classification"])
        if classification not in ("full_time", "part_time"):
            raise ConfigError(f"Invalid classification for {emp.get('employee_id')}")
        weekday_hours = _load_weekday_hours(emp.get("weekday_hours"))
        if classification == "part_time" and weekday_hours is None:
            # Incomplete on purpose — reporting returns policy_review_required.
            pass
        employees.append(
            EmployeePolicy(
                employee_id=str(emp["employee_id"]),
                classification=classification,  # type: ignore[arg-type]
                weekday_hours=weekday_hours,
            )
        )

    leave: list[LeaveRecord] = []
    for row in raw.get("leave") or []:
        leave.append(
            LeaveRecord(
                employee_id=str(row["employee_id"]),
                start=_parse_date(str(row["start"]), field_name="leave.start"),
                end=_parse_date(str(row["end"]), field_name="leave.end"),
                kind=str(row.get("kind", "leave")),  # type: ignore[arg-type]
            )
        )

    holiday = _load_holiday_policy(raw.get("holiday"))
    if holiday and holiday.mode == "calendar":
        assert holiday.calendar_id is not None
        if holiday.calendar_id not in calendars:
            raise ConfigError(
                f"Tenant {tenant_id} references unknown calendar {holiday.calendar_id}"
            )

    templates = [_load_template(t) for t in (raw.get("templates") or [])]

    return TenantConfig(
        tenant_id=tenant_id,
        label=str(raw.get("label", tenant_id)),
        region=str(raw.get("region", "SE")),
        timezone=str(raw.get("timezone", "Europe/Stockholm")),
        workbook_roots=roots,
        workbooks=tuple(workbooks),
        employees=tuple(employees),
        leave=tuple(leave),
        holiday=holiday,
        templates=tuple(templates),
        max_sends_per_tenant_per_local_day=int(
            raw.get("max_sends_per_tenant_per_local_day", 50)
        ),
        full_time_profile=str(raw.get("full_time_profile", "se_full_time_40")),
    )


def _load_clients(raw_clients: list[dict[str, Any]], tenants: dict[str, TenantConfig]) -> list[ClientRecord]:
    clients: list[ClientRecord] = []
    for raw in raw_clients:
        tenant_id = str(raw["tenant_id"])
        if tenant_id not in tenants:
            raise ConfigError(f"Client {raw.get('client_id')} references unknown tenant {tenant_id}")
        scopes = frozenset(str(s) for s in raw.get("scopes") or [])
        unknown = scopes - ALL_SCOPES
        if unknown:
            raise ConfigError(f"Unknown scopes: {sorted(unknown)}")
        clients.append(
            ClientRecord(
                client_id=str(raw["client_id"]),
                tenant_id=tenant_id,
                token_sha256=str(raw["token_sha256"]).lower(),
                scopes=scopes,
                synthetic_example=bool(raw.get("synthetic_example", False)),
            )
        )
    return clients


def _reject_llm_env(environ: dict[str, str]) -> None:
    offenders = sorted(k for k in environ if k.upper().startswith("LLM_"))
    if offenders:
        raise ConfigError(
            "LLM-prefixed environment keys are not allowed for this MCP server: "
            + ", ".join(offenders)
        )


def _sms_settings(environ: dict[str, str]) -> SmsSettings:
    provider = environ.get("SMS_PROVIDER", "mock").lower()
    if provider not in ("mock", "elks"):
        raise ConfigError(f"Unsupported SMS_PROVIDER: {provider}")
    live = environ.get("SMS_LIVE_SEND", "false").lower() in ("1", "true", "yes")
    if provider == "elks" and not live:
        raise ConfigError(
            "SMS_PROVIDER=elks requires SMS_LIVE_SEND=true; "
            "there is no silent fallback to mock"
        )
    username = environ.get("ELKS_API_USERNAME")
    password = environ.get("ELKS_API_PASSWORD")
    sender = environ.get("ELKS_FROM")
    if provider == "elks" and live and not (username and password and sender):
        raise ConfigError(
            "SMS_PROVIDER=elks with live send requires "
            "ELKS_API_USERNAME, ELKS_API_PASSWORD, and ELKS_FROM"
        )
    return SmsSettings(
        provider=provider,  # type: ignore[arg-type]
        live_send=live,
        elks_username=username,
        elks_password=password,
        elks_from=sender,
    )


def load_config(
    config_path: Path | str,
    *,
    environ: dict[str, str] | None = None,
) -> AppConfig:
    """Load and validate server configuration from TOML plus environment."""
    env = dict(os.environ if environ is None else environ)
    _reject_llm_env(env)

    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    config_dir = path.parent.resolve()

    calendars: dict[str, HolidayCalendar] = {}
    for cal_ref in raw.get("calendars") or []:
        cal_path = Path(str(cal_ref))
        if not cal_path.is_absolute():
            cal_path = (config_dir / cal_path).resolve()
        calendar = _load_calendar_file(cal_path)
        calendars[calendar.calendar_id] = calendar

    tenants: dict[str, TenantConfig] = {}
    for tenant_raw in raw.get("tenants") or []:
        tenant = _load_tenant(tenant_raw, config_dir=config_dir, calendars=calendars)
        if tenant.tenant_id in tenants:
            raise ConfigError(f"Duplicate tenant_id {tenant.tenant_id}")
        tenants[tenant.tenant_id] = tenant

    if not tenants:
        raise ConfigError("At least one tenant is required")

    clients = PrincipalRegistry(_load_clients(list(raw.get("clients") or []), tenants))

    stdio_tenant = env.get("TIMEREPORT_STDIO_TENANT") or None
    if stdio_tenant is not None and stdio_tenant not in tenants:
        raise ConfigError(
            f"TIMEREPORT_STDIO_TENANT={stdio_tenant!r} is not a configured tenant"
        )

    data_dir = Path(env.get("TIMEREPORT_DATA_DIR", "data")).resolve()
    database_url = env.get("DATABASE_URL", f"sqlite:///{data_dir / 'timereport.db'}")

    settings = ServerSettings(
        data_dir=data_dir,
        database_url=database_url,
        http_host=env.get("TIMEREPORT_HTTP_HOST", "127.0.0.1"),
        http_port=int(env.get("TIMEREPORT_HTTP_PORT", "8000")),
        resource_server_url=env.get(
            "TIMEREPORT_RESOURCE_SERVER_URL", "http://127.0.0.1:8000/mcp"
        ),
        auth_issuer_url=env.get(
            "TIMEREPORT_AUTH_ISSUER_URL", "https://auth.example.invalid"
        ),
        stdio_tenant=stdio_tenant,
        sms=_sms_settings(env),
    )

    return AppConfig(
        tenants=tenants,
        clients=clients,
        calendars=calendars,
        settings=settings,
        config_path=path.resolve(),
    )


def require_stdio_tenant(config: AppConfig) -> str:
    """Return the bound stdio tenant or raise if unset/unknown."""
    tenant = config.settings.stdio_tenant
    if not tenant:
        raise ConfigError(
            "TIMEREPORT_STDIO_TENANT is required for stdio transport"
        )
    if tenant not in config.tenants:
        raise ConfigError(f"Unknown stdio tenant {tenant}")
    return tenant
