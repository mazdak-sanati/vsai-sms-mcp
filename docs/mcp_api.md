# MCP API Reference — TimeReport Server (Phase 1)

Consumer-facing tool reference for authenticated MCP clients. This is **not** an agent prompt. Tenant identity comes from the connection (HTTP bearer token or stdio binding), never from tool arguments.

Synthetic examples use invented employees and the mock SMS provider.

## Authentication

| Transport | Identity |
|---|---|
| Streamable HTTP | `Authorization: Bearer <token>` verified against server-side SHA-256 hashes |
| stdio | Single tenant from `TIMEREPORT_STDIO_TENANT` at process start |

Scopes: `workbooks:read`, `workbooks:write`, `sms:read`, `sms:send`, `sms:admin`.

## Result envelope

```json
{ "ok": true, "data": { } }
```

```json
{ "ok": false, "error": { "code": "not_found", "message": "...", "details": {} } }
```

## Tools

### `list_workbooks`

**Scope:** `workbooks:read`  
**Input:** none

**Example result:**

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

**Scope:** `workbooks:read`  
**Input:** `workbook_id` (string)

### `read_timesheet_entries`

**Scope:** `workbooks:read`  
**Input:** `workbook_id`, `period` (`YYYY-MM`)

### `get_reporting_status`

**Scope:** `workbooks:read`  
**Input:** `workbook_id`, `period`, optional `employee_id`

**Example (synthetic October 2026):**

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
        "reported_hours": 176,
        "status": "complete",
        "policy_warnings": []
      }
    ]
  }
}
```

Statuses: `complete`, `incomplete`, `policy_review_required`, `data_review_required`.

### `update_timesheet_entry`

**Scope:** `workbooks:write`  
**Input:** `workbook_id`, `employee_id`, `date` (`YYYY-MM-DD`), `hours`, `expected_version`, optional `create_if_missing` (default false)

Requires the current SHA-256 file version from a prior read/inspect.

### `get_sms_status`

**Scope:** `sms:read`  
**Input:** `employee_id`, `period`, optional `local_date`

### `send_sms`

**Scope:** `sms:send`  
**Input:** `workbook_id`, `employee_id`, `period`, `template_id`

At-most-once per tenant + employee + period + local calendar day (`Europe/Stockholm`). Repeats return the stored claim with `replayed: true`. Claims are committed as `pending` before the provider call; timeouts become `uncertain` and are not resent the same day.

**Example (mock):**

```json
{
  "ok": true,
  "data": {
    "employee_id": "synthetic-employee-a1",
    "period": "2026-10",
    "local_date": "2026-10-15",
    "template_id": "timesheet_reminder",
    "state": "sent",
    "provider": "mock",
    "provider_message_id": "mock-1",
    "replayed": false
  }
}
```

### `set_sms_suppression`

**Scope:** `sms:admin`  
**Input:** `employee_id`, `reason` (`opt_out` | `admin`), `active` (boolean)

`opt_out` cannot be cleared through this tool.

## Error codes

`forbidden`, `not_found`, `validation_error`, `workbook_unreadable`, `schema_mismatch`, `formula_not_supported`, `version_conflict`, `lock_timeout`, `sms_suppressed`, `sms_budget_exceeded`, `sms_not_eligible`, `provider_rejected`, `live_send_disabled`.
