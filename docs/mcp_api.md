# TimeReport MCP — Client & Agent Usage Guide

This is the **consumer contract** for any MCP client that connects to this server, including a future Phase 2 runtime agent.

This file is **not** a system prompt and does **not** define reminder cadence, LLM choice, or business policy for *when* to message people. Those decisions belong to the calling agent. This server only exposes tools and enforces tenant/workbook/SMS safety rules when a tool is invoked.

Full tool table (compact): [`mcp_interface.md`](mcp_interface.md).

---

## 1. What this server does

| Capability | Tools |
|---|---|
| Discover tenant workbooks | `list_workbooks` |
| Validate / read timesheets | `inspect_timesheet`, `read_timesheet_entries` |
| Expected vs reported hours | `get_reporting_status` |
| Controlled hours update | `update_timesheet_entry` |
| On-demand SMS | `get_sms_status`, `send_sms`, `set_sms_suppression` |

The server **never** starts a schedule, polls workbooks by itself, or sends SMS unless a client calls `send_sms`.

---

## 2. How to connect

### Streamable HTTP (multi-tenant)

```text
URL:    http://127.0.0.1:8000/mcp   (or your configured host/port)
Header: Authorization: Bearer <token>
```

Start:

```bash
set TIMEREPORT_CONFIG=config/tenants.example.toml
set SMS_PROVIDER=mock
timereport-mcp --transport streamable-http
```

Tenant and scopes come from the verified token in server config (`[[clients]]` → `token_sha256`). Do **not** pass `tenant_id` in tool arguments.

Synthetic example tokens (local loopback / tests only):

| Token (plaintext) | Tenant |
|---|---|
| `tenant-a-token-synthetic` | `tenant-a` |
| `tenant-b-token-synthetic` | `tenant-b` |

Replace these before any non-local deployment.

### stdio (single tenant)

```bash
set TIMEREPORT_CONFIG=config/tenants.example.toml
set TIMEREPORT_STDIO_TENANT=tenant-a
set SMS_PROVIDER=mock
timereport-mcp --transport stdio
```

`TIMEREPORT_STDIO_TENANT` is required. The process is bound to that one tenant for the whole session.

### Cursor / MCP host config (example)

Point the host at the installed console script or module. Exact JSON shape depends on the host; conceptually:

- command: the `timereport-mcp` executable (or `python -m timereport_mcp.server`)
- env: `TIMEREPORT_CONFIG`, `TIMEREPORT_STDIO_TENANT` (for stdio), `SMS_PROVIDER`, optional `DATABASE_URL` / `TIMEREPORT_DATA_DIR`
- for HTTP: configure the MCP URL and bearer token in the host’s HTTP/auth settings

LLM API keys belong only in the **agent** process, never in this server’s environment.

---

## 3. Rules every client must follow

1. **Do not invent filesystem paths.** Use `workbook_id` values from `list_workbooks`.
2. **Do not pass `tenant_id`.** Tenant is implied by the connection.
3. **Do not pass SMS API secrets as tool arguments.** Provider credentials are server env (`ELKS_*`, `SMS_PROVIDER`, `SMS_LIVE_SEND`).
4. **Periods** are `YYYY-MM`. **Dates** are `YYYY-MM-DD`. Timezone for SMS local day and reporting calendar is tenant timezone (default `Europe/Stockholm`).
5. **Completion** is only from Excel + policy via `get_reporting_status`. An SMS reply is not timesheet completion.
6. After any successful `update_timesheet_entry`, use the returned `version` (or re-`inspect_timesheet`) before the next write.
7. Always read tool results as the JSON envelope below. Check `ok` before using `data`.

---

## 4. Result envelope

Success:

```json
{ "ok": true, "data": { }, "error": null }
```

Failure:

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "not_found",
    "message": "Workbook not found.",
    "details": {}
  }
}
```

Unknown workbook ids and cross-tenant ids both return `not_found` (no existence leak).

### Error codes

| Code | Meaning |
|---|---|
| `forbidden` | Missing auth or scope |
| `not_found` | Workbook / employee / template not visible |
| `validation_error` | Bad argument shape or value |
| `workbook_unreadable` | File missing, corrupt, or over row limit |
| `schema_mismatch` | Required columns/sheet missing |
| `formula_not_supported` | Formula in hours (or related) cell |
| `version_conflict` | `expected_version` stale |
| `lock_timeout` | Could not lock workbook for write |
| `sms_suppressed` | Opt-out or admin suppression active |
| `sms_budget_exceeded` | Tenant daily send budget hit |
| `sms_not_eligible` | Phone not valid E.164 |
| `provider_rejected` | Provider definitive 4xx (when exposed) |
| `live_send_disabled` | Live provider not enabled |

---

## 5. Suggested workflows

### A. Check who is incomplete for a month

1. `list_workbooks` → pick `workbook_id`
2. `get_reporting_status(workbook_id, period="YYYY-MM")`
3. For each employee with `status` = `incomplete`, the calling agent may choose to notify
4. Optional: `get_sms_status` before `send_sms` to see today’s claim / suppression

### B. Safe hours correction

1. `inspect_timesheet` or `read_timesheet_entries` → note `version`
2. `update_timesheet_entry(..., expected_version=<version>)`
3. On `version_conflict`, re-read and retry once with the new version

### C. Send a reminder SMS

1. Calling agent chooses recipient phone and composes the message body (from its own queue/CSV/policy)
2. Optional: `get_sms_status` to see today’s claim / suppression
3. `send_sms(to, message, employee_id, period)`
4. Interpret result:
   - `ok` + `replayed: false` → provider was invoked for this claim
   - `ok` + `replayed: true` → **no new SMS**; a claim already existed for that employee/period/**local calendar day**
   - `ok: false` + `sms_suppressed` / `sms_budget_exceeded` / `sms_not_eligible` / `validation_error` → do not retry blindly

**Important:** The calling agent decides *who*, *what*, *whether*, and *when*. This server only delivers the provided `to`/`message` and enforces at-most-once delivery per tenant + employee + period + local day so retries are safe. A second call the same day will not send a second message.

---

## 6. Tools (schemas and examples)

Scopes required per tool are listed. Missing scope → `forbidden`.

### `list_workbooks`

- **Scope:** `workbooks:read`
- **Input:** none
- **Returns:** opaque `workbook_id`, label, sheet name, mapped column names (never absolute paths)

```json
{
  "ok": true,
  "data": {
    "workbooks": [
      {
        "workbook_id": "tenant-a-timesheet",
        "label": "Tenant A timesheet",
        "sheet": "Timesheet",
        "columns": {
          "employee_name": "employee_name",
          "telephone": "telephone",
          "date": "date",
          "hours": "hours",
          "employee_id": "employee_id"
        }
      }
    ]
  }
}
```

### `inspect_timesheet`

- **Scope:** `workbooks:read`
- **Input:** `workbook_id` (string)
- **Returns:** schema quality, row/issue counts, content `version` (SHA-256 of file bytes)

### `read_timesheet_entries`

- **Scope:** `workbooks:read`
- **Input:**
  - `workbook_id` (string)
  - `period` (string, `YYYY-MM`)
- **Returns:** entries for that month: `employee_id`, `employee_name`, `date`, `hours` (telephone is not returned in the entry list payload used for reporting)

### `get_reporting_status`

- **Scope:** `workbooks:read`
- **Input:**
  - `workbook_id` (string)
  - `period` (string, `YYYY-MM`)
  - `employee_id` (string, optional)
- **Returns:** per-employee `expected_hours`, `reported_hours`, `status`, `policy_warnings`

Statuses:

| Status | Meaning |
|---|---|
| `complete` | Reported hours ≥ expected |
| `incomplete` | Reported hours &lt; expected |
| `policy_review_required` | Expected hours unknown (missing holiday/part-time policy, etc.); do not treat as incomplete |
| `data_review_required` | Invalid/duplicate rows; do not trust totals |

Swedish full-time baseline when policy is complete: **40 h/week** (8 h Mon–Fri), adjusted for configured holidays and approved leave. Example: October 2026 with no holidays → 176 expected hours.

```json
{
  "ok": true,
  "data": {
    "workbook_id": "tenant-a-timesheet",
    "period": "2026-10",
    "employees": [
      {
        "employee_id": "synthetic-employee-a1",
        "employee_name": "Synthetic Person A",
        "expected_hours": 176,
        "reported_hours": 168,
        "status": "incomplete",
        "policy_warnings": []
      }
    ]
  }
}
```

### `update_timesheet_entry`

- **Scope:** `workbooks:write`
- **Input:**
  - `workbook_id` (string)
  - `employee_id` (string)
  - `date` (string, `YYYY-MM-DD`)
  - `hours` (number, 0–24, ≤ 2 decimals)
  - `expected_version` (string, SHA-256 from prior read)
  - `create_if_missing` (boolean, default `false`)
- **Side effect:** writes one hours cell; creates backup; updates audit trail
- **Returns:** new `version`

### `get_sms_status`

- **Scope:** `sms:read`
- **Input:**
  - `employee_id` (string)
  - `period` (string, `YYYY-MM`)
  - `local_date` (string `YYYY-MM-DD`, optional; default today in tenant TZ)
- **Returns:** claim state (if any) and suppression state. Does not return message body or raw phone.

### `send_sms`

- **Scope:** `sms:send`
- **Input:**
  - `to` (string) — destination telephone (prefer E.164); normalized server-side
  - `message` (string) — exact SMS body composed by the calling agent (max 480 chars)
  - `employee_id` (string) — for suppression + idempotency only (not used to look up phone)
  - `period` (string, `YYYY-MM`) — for idempotency only
- **Side effect:** may send one SMS via the configured provider (`mock` or `elks`)
- **Idempotency key:** `tenant_id + employee_id + period + local civil date`
- **Returns:** claim with `state` (`pending` \| `sent` \| `rejected` \| `uncertain`), `replayed`, optional `destination_last4` / `provider_detail`

```json
{
  "ok": true,
  "data": {
    "employee_id": "synthetic-employee-a1",
    "period": "2026-10",
    "local_date": "2026-10-15",
    "template_id": "caller_provided",
    "state": "sent",
    "provider": "mock",
    "provider_message_id": "mock-1",
    "replayed": false,
    "destination_last4": "0001"
  }
}
```

This server does **not** choose recipients or compose reminder text. Credentials are never tool arguments.

### `set_sms_suppression`

- **Scope:** `sms:admin`
- **Input:**
  - `employee_id` (string)
  - `reason` (`opt_out` \| `admin`)
  - `active` (boolean)
- **Notes:** `opt_out` cannot be cleared via this tool. Active suppression blocks `send_sms`.

---

## 7. SMS provider configuration (server-side)

| Env | Purpose |
|---|---|
| `SMS_PROVIDER` | `mock` (default) or `elks` |
| `SMS_LIVE_SEND` | must be `true` for `elks` |
| `ELKS_API_USERNAME` | 46elks API user |
| `ELKS_API_PASSWORD` | 46elks API password |
| `ELKS_FROM` | sender id / number |

Clients never send these as tool parameters. For a manual live check outside the agent, see `scripts/live_sms_mcp_test.py` and `tests/local_test.env.example`.

---

## 8. What this server will not do for you

- Choose check frequency or reminder loops
- Call an LLM or store prompts
- Accept absolute paths or client-declared tenant ids
- Treat an SMS reply as a completed timesheet
- Send a second automated SMS for the same employee/period/local day when a claim already exists

The Phase 2 agent owns scheduling, recipient selection, message composition, and “should we remind?” policy. This MCP owns safe Excel access and executing an authorized `send_sms(to, message, …)` under server rules (suppression, budget, idempotency, provider credentials).
