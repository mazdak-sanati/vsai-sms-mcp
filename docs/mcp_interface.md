# MCP Interface Contract — Tool Summary

Compact contract for MCP clients. **How to connect, workflows, and examples:** [`mcp_api.md`](mcp_api.md).

This is **not** a runtime agent system prompt. It does not define Excel paths, reminder cadence, LLM settings, or `.env` secrets for Phase 2.

**All tools run only when an authenticated MCP client invokes them.** The server never self-schedules checks or SMS.

## Tools

| Tool | Behavior | Side effects | Scope |
|---|---|---|---|
| `list_workbooks` | Tenant-authorized workbook ids + column metadata | None | `workbooks:read` |
| `inspect_timesheet` | Validate schema/quality; return content `version` | None | `workbooks:read` |
| `read_timesheet_entries` | Entries for `period` (`YYYY-MM`) | None | `workbooks:read` |
| `get_reporting_status` | Expected vs reported hours; status per employee | None | `workbooks:read` |
| `update_timesheet_entry` | Change one hours cell with `expected_version` | Workbook write + backup + audit | `workbooks:write` |
| `get_sms_status` | Claim + suppression state | None | `sms:read` |
| `send_sms` | Resolve phone from workbook; send via template; daily claim key | May send SMS | `sms:send` |
| `set_sms_suppression` | Record `opt_out` / `admin` suppression | Persistence write | `sms:admin` |

## Input principles

- Use opaque `workbook_id` / `employee_id` from tools or workbook data — never absolute paths.
- Tenant comes from the connection (HTTP bearer token or stdio binding), not tool args.
- Periods: `YYYY-MM`. Dates: `YYYY-MM-DD`. Default timezone: `Europe/Stockholm`.
- No tool accepts an LLM API key or SMS password.
- `update_timesheet_entry` requires current file `version`.
- `send_sms` is at-most-once per tenant + employee + period + local day; repeats return `replayed: true`.

## Reporting statuses

`complete` · `incomplete` · `policy_review_required` · `data_review_required`

## Safety invariants

- Cross-tenant ids → `not_found`.
- Opt-out / admin suppression blocks further sends.
- SMS reply ≠ timesheet completion; only verified Excel + policy decide status.
- Synthetic fixtures + `SMS_PROVIDER=mock` are enough for integration without live SMS.
