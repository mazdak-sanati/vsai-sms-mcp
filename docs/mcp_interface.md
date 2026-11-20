# MCP Interface Contract — For Future Clients (Not Agent Instructions)

## Purpose and scope
This defines the **Phase 1 server-side tool interface**. A future MCP client, including an eventual Phase 2 agent, may call these tools, but this document does not define a runtime agent's prompts, Excel paths, schedule, LLM settings, or `.env`.

**All tools operate only when invoked by an authenticated, authorized external MCP client.** The server never initiates calendar-based checks or messages by itself.

For request/response examples see `docs/mcp_api.md`.

## Phase 1 MCP tools
| Tool | Server behavior | Side effects | Scope |
|---|---|---|---|
| `list_workbooks` | Return tenant-authorized workbook identifiers and schema metadata | None | `workbooks:read` |
| `inspect_timesheet` | Validate workbook columns/types, summarize record quality | None | `workbooks:read` |
| `get_reporting_status` | Calculate expected vs reported hours for a period and employee(s) | None | `workbooks:read` |
| `read_timesheet_entries` | Return only authorized minimal reporting entries | None | `workbooks:read` |
| `update_timesheet_entry` | Explicitly change a permitted entry with precondition/version; audit | Workbook write | `workbooks:write` |
| `get_sms_status` | Query send, suppression, delivery and opt-out state | None | `sms:read` |
| `send_sms` | Validate recipient/template, eligibility, scope, budget and unique daily key; dispatch via provider | **External SMS**, explicitly enabled | `sms:send` |
| `set_sms_suppression` | Record opt-out or administrative suppression | Persistence write | `sms:admin` |

## Tool input principles
- Tool calls use opaque server-issued workbook/employee identifiers, not arbitrary filesystem paths.
- Tenant identity comes from authenticated connection context, not a `tenant_id` string in caller input.
- Reporting period is explicit (`YYYY-MM`); date semantics use configured local timezone (initially Europe/Stockholm).
- No tool takes an LLM API key. No tool returns provider API secrets.
- Workbook mutations require intended field changes and an expected version. Preserve unrelated sheets and data where technically possible.
- `send_sms` requires authorization for the recipient and reporting period; the service enforces daily uniqueness (tenant + employee + period + local date), rate/budget controls and opt-outs. Repeated requests return previously recorded state, not a second SMS. Provider timeout/uncertain outcomes are not automatically resent without reconciliation.

## Reporting statuses
`complete`, `incomplete`, `policy_review_required`, `data_review_required`.

## Safety invariants
- All calls are authorized, scoped, validated, recorded appropriately and PII-minimized.
- A normal SMS reply is not a timesheet submission. STOP/opt-out or administrative suppression blocks further sends even if hours remain missing.
- Every tool can be exercised using generated Excel fixtures and a fake SMS gateway without real credentials or live recipients.
- Actual Excel files, phone numbers, SMS credentials, LLM credentials, and any reminder cadence are supplied/managed outside this repository by **Phase 2**.
