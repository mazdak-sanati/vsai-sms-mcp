# Plan — Phase 1 TimeReport MCP Server

**Status:** Approved and implemented (M0–M8).

**Recorded:** 2026-11-19 (Europe/Stockholm)

**Approval phrase:** `Implement the approved docs/plan.md`

This plan covers only `vectorsynapse-timereport-mcp-server`: callable MCP tools, tenant-scoped local `.xlsx` access, expected-hours calculation, and an on-demand SMS tool with a mock provider. Phase 2 owns the runtime agent, real workbooks, real secrets, and any decision about when to call these tools.

## 1. Repository findings

The repository contains `AGENTS.md` and `docs/` only. There is no application source, test suite, `pyproject.toml`, `docs/plan.md` (before this file), or `docs/status.md` (before this cycle). No sample workbook is present, so the column contract below is the one named in `AGENTS.md`, expressed as a canonical synthetic fixture. A customer file whose headers are not in that workbook's server-side mapping fails validation.

Local interpreter check during planning: Python 3.12.10. Dependencies were not installed.

## 2. Stack validation

Checked against the official Python SDK docs on 2026-11-19 (`https://py.sdk.modelcontextprotocol.io/`, PyPI `mcp` 2.3.0).

| Concern | Decision | Evidence |
|---|---|---|
| Language | Python 3.12+ | Local interpreter is 3.12.10. SDK installation docs require 3.11+; 3.12 satisfies that floor. |
| MCP | `mcp>=2.3.0,<3` | Current stable line is v2. High-level class is `mcp.server.MCPServer`. The v1 `FastMCP` import path is removed. Re-read the v2 changelog at the start of milestone M2 before pinning the installed version. |
| Tool schemas | Pydantic v2, via the SDK | Tool arguments and structured results use type hints. `Context` is injected by annotation and is omitted from the tool schema. |
| HTTP server | Starlette + uvicorn, started by `MCPServer.run(transport="streamable-http")` | SDK default deploy transport. SSE is legacy and is not a Phase 1 transport. |
| Workbook | `openpyxl` | Reads and writes `.xlsx`. It does not evaluate formulas. Formula cells are reported as errors. |
| State | SQLAlchemy 2 + Alembic | Schema, transactions, and a uniqueness constraint for SMS claims. |
| Default database | SQLite file under the server data directory | Phase 1 acceptance is local and must not require a database server. WAL mode and a unique constraint give durable at-most-once claims for the single-process acceptance target. |
| Production database | PostgreSQL through the same models and migrations when `DATABASE_URL` is a PostgreSQL URL | Optional. A Postgres run is enabled only when `TEST_DATABASE_URL` is set. It is not part of the definition of done. |
| SMS | `SmsGateway` protocol, in-process mock, optional 46elks adapter | 46elks `POST https://api.46elks.com/a1/sms` uses HTTP Basic auth and form fields `from`, `to`, `message`. The API has no idempotency key. It does have `dryrun=yes`, which still requires credentials and a network call, so tests never use it. |
| Outbound HTTP | `httpx` inside the 46elks adapter only | Timeouts are explicit. Automatic retries are off. The SDK's own HTTP stack is a separate matter and is not used for SMS. |
| Phone numbers | `phonenumbers` | Normalize to E.164. Ambiguous numbers are rejected. |
| File locking | `filelock` | Exclusive lock usable on Windows and POSIX. This machine is Windows. |
| Tests | `pytest`, `pytest-asyncio` | In-memory `mcp.Client` plus localhost Streamable HTTP tests. |
| Lint and types | `ruff`, `pyright` | Run in M0 onward. |
| Config files | TOML read with the stdlib `tomllib` | No extra parser dependency. |

`docs/tech_stack.md` remains the original proposal. This section is the decision record.

### Dependency boundaries

Phase 1 installs no LLM SDK and no scheduler library. The SMS adapter reads only SMS credentials. Configuration keys for an LLM provider are rejected at startup if present, so a copied Phase 2 environment cannot be silently consumed.

## 3. Architecture

```text
MCP client
  │  stdio (one tenant bound at process start)
  │  or Streamable HTTP Bearer token
  ▼
MCPServer tool wrappers          auth: principal + scope
  ▼
reporting / excel / sms services
  ▼
allowlisted .xlsx    SQLite or PostgreSQL    SmsGateway
                     claims, suppressions,
                     audit
```

Module layout follows `docs/code_structure.md`, with these additions:

- `migrations/` at the repository root for Alembic.
- `config/tenants.example.toml` and `config/calendars/` for synthetic policy data.
- `docs/mcp_api.md` and `docs/handoff.md`, written in milestone M8.

Tool functions in `server.py` validate the caller and delegate. They do not compute policy and they do not open files by a caller-supplied path.

### Transport and tenant identity

Two modes:

1. **Streamable HTTP** is the multi-tenant mode. `stateless_http=True` and `json_response=True`. The process binds to `127.0.0.1` unless the operator sets a host. The server is an OAuth 2.1 resource server as defined by the SDK: it verifies bearer tokens and it does not issue them, and it does not embed an authorization server (`auth_server_provider` is out of use).
2. **stdio** is the local single-tenant mode. The SDK does not consult `TokenVerifier` on stdio, and `get_access_token()` is always `None`. Startup requires `TIMEREPORT_STDIO_TENANT` to name exactly one tenant in the server config. The process installs that tenant as the only principal. A config that does not contain that tenant prevents startup.

HTTP verification:

- Implement `TokenVerifier.verify_token`. Compare the bearer token with SHA-256 hashes stored in the server config. The example file contains hashes of published synthetic tokens, never raw production tokens.
- On success, return an `AccessToken` whose `client_id`, scopes, and `resource` come from the server registry. A claim inside the token string cannot choose a tenant.
- `AuthSettings.validate_token_resource=True`, with `resource_server_url` equal to the configured public MCP URL.
- Each `client_id` maps to exactly one `tenant_id` and a scope set: `workbooks:read`, `workbooks:write`, `sms:read`, `sms:send`, `sms:admin`.
- Unknown tokens return `None`. The SDK answers `401` before a tool runs.

Principal resolution inside every tool:

1. If `get_access_token()` returns a token, the principal is the registry entry for that `client_id`.
2. Otherwise, if the process is the stdio server and the startup principal is installed, use that principal.
3. Otherwise deny the call.

An in-memory `mcp.Client` skips HTTP auth. Tests of the service layer pass an explicit principal. Tests of the HTTP boundary use two real bearer tokens. A test-only context override is ignored whenever `get_access_token()` is present, so a process-local override cannot replace an HTTP caller.

Client-supplied `tenant_id`, filesystem paths, `Context` headers, and `_meta` are never identity. `Context.client_id` was removed by the SDK because it was caller-controlled; this server does not reintroduce that pattern.

Unknown workbook ids and workbook ids owned by another tenant return the same `not_found` result.

### Capabilities

| Scope | Tools |
|---|---|
| `workbooks:read` | `list_workbooks`, `inspect_timesheet`, `read_timesheet_entries`, `get_reporting_status` |
| `workbooks:write` | `update_timesheet_entry` |
| `sms:read` | `get_sms_status` |
| `sms:send` | `send_sms` |
| `sms:admin` | `set_sms_suppression` |

A missing scope returns `forbidden` and performs no side effect.

## 4. Tool contract

Final Phase 1 tools. Inputs never include a tenant id, an absolute path, a telephone number, a message body, or an API key.

| Tool | Required input | Result |
|---|---|---|
| `list_workbooks` | none | Opaque workbook ids, labels, and mapped column names for the caller’s tenant. Paths are omitted. |
| `inspect_timesheet` | `workbook_id` | Schema findings, row counts, and data-quality codes. |
| `read_timesheet_entries` | `workbook_id`, `period` (`YYYY-MM`) | `employee_id`, `employee_name`, `date`, `hours` for rows in that month. |
| `get_reporting_status` | `workbook_id`, `period`, optional `employee_id` | Expected hours, reported hours, and a status per employee. |
| `update_timesheet_entry` | `workbook_id`, `employee_id`, `date`, `hours`, `expected_version`, optional `create_if_missing` (default false) | New version, or a conflict. Writes hours only. |
| `get_sms_status` | `employee_id`, `period`, optional `local_date` | Claim state and suppression state. Message body is omitted. |
| `send_sms` | `workbook_id`, `employee_id`, `period`, `template_id` | Dispatch or the previously recorded claim. |
| `set_sms_suppression` | `employee_id`, `reason` (`opt_out` or `admin`), `active` | Audited suppression change. |

`set_sms_suppression` is an addition to the sketch in `docs/mcp_interface.md`. The safety rules require opt-out and administrative suppression to block sends. Phase 1 has no inbound SMS webhook, so an authorized client needs one explicit tool to record that state. `opt_out` cannot be cleared through this tool. `admin` can be set inactive by a later call. Inbound STOP handling stays on the improvement backlog.

Domain results are structured objects:

```json
{
  "ok": true,
  "data": {}
}
```

```json
{
  "ok": false,
  "error": {"code": "version_conflict", "message": "Workbook version did not match.", "details": {}}
}
```

Pydantic rejects malformed types before the handler. Handlers return `ok: false` for domain failures. A successful calculation whose status is `policy_review_required` or `data_review_required` is `ok: true`, because the server did determine the state. A repeated `send_sms` is `ok: true` with the stored claim and `replayed: true`.

Stable error codes: `forbidden`, `not_found`, `validation_error`, `workbook_unreadable`, `schema_mismatch`, `formula_not_supported`, `version_conflict`, `lock_timeout`, `sms_suppressed`, `sms_budget_exceeded`, `sms_not_eligible`, `provider_rejected`, `live_send_disabled`.

`docs/mcp_interface.md` is updated in M8 so its tool list matches this contract. `docs/mcp_api.md` becomes the consumer-facing reference with synthetic request and response examples.

## 5. Workbook contract

Canonical logical columns:

| Field | Required | Accepted in the synthetic fixture |
|---|---|---|
| `employee_name` | yes | header `employee_name` |
| `telephone` | yes | header `telephone` |
| `date` | yes | header `date` |
| `hours` | yes | header `hours` |
| `employee_id` | no | header `employee_id` when the operator enables it |

Extra aliases are accepted only when that workbook’s server config lists them. The server does not fuzzy-match headers and does not infer that two similar names are one person.

Identifier rules:

- `workbook_id` is an opaque id from the tenant registry.
- The config path is resolved and must stay inside `server.workbook_roots` after symlink resolution. A path outside the roots prevents startup.
- `employee_id` is the configured id column when present and non-empty. Otherwise it is a slug of the casefolded, whitespace-collapsed name, and `inspect_timesheet` includes the warning `employee_identity_is_name_derived`.

Cell rules:

- Dates are Excel date cells or exact `YYYY-MM-DD` text. Locale-ambiguous text such as `01/02/2026` is a validation error.
- Hours are numbers from `0` through `24` inclusive, with at most two decimal places. Booleans are rejected. Blank hours on an existing row are `hours_missing`.
- A formula in a hours cell yields `formula_not_supported`. Cached formula values are not trusted.
- More than 50 000 data rows yields `workbook_unreadable` for that call.
- Duplicate `(employee_id, date)` rows make the reporting status `data_review_required` and prevent a write to that key until the sheet has one row for the key.
- A file `openpyxl` cannot load yields `workbook_unreadable`.

### Controlled writes

`update_timesheet_entry` changes the hours cell for one employee and one civil date.

1. Resolve the allowlisted path.
2. Take an exclusive `filelock` beside the server’s lock directory for that workbook, with a 10 second timeout (`lock_timeout`).
3. Hash the file bytes with SHA-256. That hex digest is `version`.
4. Refuse the call when `expected_version` differs (`version_conflict`).
5. Refuse the call when the target cell is a formula.
6. When the row is absent, create it only if `create_if_missing` is true. New rows receive the mapped identity, date, and hours cells only.
7. Copy the current file into `{data_dir}/backups/{tenant_id}/{workbook_id}/{version}.xlsx` before replacement. A failed backup aborts the write. Keep the newest 20 backups per workbook.
8. Load the workbook with `openpyxl`, edit the one cell, save to a temporary file in the same directory as the target, flush it, and `os.replace` it over the target. Other sheets, columns, and rows stay in the workbook that `openpyxl` loaded.
9. On Windows, `os.replace` is the atomic same-volume replace. Directory fsync is applied on POSIX and skipped on Windows, where that call is not available.
10. Append an audit event containing actor `client_id`, workbook id, employee id, date, old hours, new hours, and the new version. The audit omits telephone numbers and message text.
11. Release the lock.

Recovery in Phase 1 is an operator procedure: stop writers, replace the workbook file with a chosen backup while the lock is free, and confirm the new hash through `inspect_timesheet`. There is no restore tool in this phase.

Reads do not take the exclusive lock. A reader observes either the old file or the replaced file.

## 6. Reporting policy

Timezone for the reporting period and the SMS calendar day is `Europe/Stockholm`. A test clock can freeze the civil date.

`period` `YYYY-MM` is the inclusive civil-month range. October 2026 is the synthetic reference month: Thursday 2026-10-01 through Saturday 2026-10-31 contains 22 Monday–Friday dates, so the full-time baseline is 176 hours. February 2026 contains 20 such dates, so the same profile is 160 hours. The monthly total is computed from the calendar. It is not a stored constant.

Profiles and exceptions live in server config, not in workbook prose.

`se_full_time_40` means 8 hours on each Monday–Friday and 0 on Saturday and Sunday, minus dates covered by a configured holiday calendar and minus approved leave or sick records.

An employee policy is complete only when all of the following hold:

- The employee classification is `full_time` or `part_time`.
- `full_time` uses the tenant profile `se_full_time_40`.
- `part_time` has an explicit hour value for each weekday. A single weekly total is not split automatically.
- The tenant holiday setting is either a calendar whose covered range includes the requested month, or the explicit assertion `none_confirmed` for a range that includes the month. An absent holiday setting is incomplete. The repository ships synthetic calendars for tests and does not ship a legal Swedish holiday table.
- Leave and sick reduce expected hours only when an approved record names the employee, the dates, and the effect. A blank hours cell is not leave. Cell comments are not interpreted.

When policy is incomplete, `expected_hours` is `null` and `status` is `policy_review_required`. The server does not also label that employee `incomplete`.

When policy is complete:

- `reported_hours` is the sum of valid numeric hours on dates in the month.
- `complete` when `reported_hours >= expected_hours`. Hours above the baseline add the warning `hours_above_expected`.
- `incomplete` when `reported_hours < expected_hours`.
- `data_review_required` when any in-period row is invalid or duplicated. This status replaces `complete` and `incomplete` for that employee, so an untrustworthy total is not presented as a finished judgment.

`data_review_required` is an addition to the three statuses in the interface sketch. It keeps corrupt sheets distinct from missing policy.

An SMS reply is not an input to this calculation. Completion is the workbook comparison above.

## 7. SMS

`send_sms` is a tool. Startup does not schedule a loop, and no module imports a scheduler.

### Provider selection

| `SMS_PROVIDER` | `SMS_LIVE_SEND` | Behavior |
|---|---|---|
| `mock` (default) | ignored | In-process fake. No network. |
| `elks` | `false` | Process refuses to start. There is no silent fallback to mock. |
| `elks` | `true` | Live adapter, only when username, password, and sender id are present. Still requires an authorized `send_sms` call. |
| any other value | any | Process refuses to start. |

Acceptance tests force `SMS_PROVIDER=mock`. The 46elks unit tests use `httpx.MockTransport`. They do not call `api.46elks.com`. `dryrun` is documented for a future operator check and is not a test path.

Adapter request: `POST /a1/sms` with a 10 second timeout, no retry transport, and `dontlog=message`. Basic-auth material is held in memory for the adapter and is excluded from logs, exceptions returned to the client, and audit rows.

### Templates

Templates are server config. The body may contain the placeholder `{period}` only. Employee names, telephone numbers, and workbook cell text are not interpolated. A template longer than 480 characters fails config load. The caller selects `template_id`.

### Eligibility, in order

1. Caller has `sms:send`, and the workbook and employee belong to the caller’s tenant.
2. Template exists.
3. The employee has a telephone value that normalizes to E.164. Swedish national `07…` numbers are accepted when the tenant region is `SE`. Other forms that `phonenumbers` cannot make unambiguous are rejected.
4. An active `opt_out` or `admin` suppression blocks the send. No claim row is inserted.
5. The tenant’s `max_sends_per_tenant_per_local_day` (default 50) counts claim rows in `pending`, `sent`, `rejected`, and `uncertain` for that local date. Over the cap, no claim is inserted and the code is `sms_budget_exceeded`.

### At-most-once claim

Unique key: `tenant_id + employee_id + period + local civil date`. The template id is not part of the key. One automated send may be attempted for that key on a given Stockholm calendar day.

46elks cannot dedupe a retried POST. The local transaction is the dedupe boundary, and the uncertainty policy is deliberate:

1. Insert a `pending` claim and commit it before any provider call.
2. A second caller that hits the unique constraint loads the existing row and returns it. It does not call the provider.
3. The winner calls the provider once.
4. `2xx` with a message id becomes `sent`.
5. A definite HTTP 4xx becomes `rejected`.
6. Timeout, connection loss, HTTP 5xx, or a 2xx body without an id becomes `uncertain`.
7. `pending` is terminal for `send_sms`. A process crash after the commit and before a durable provider result leaves `pending`. The next call returns that row and does not send. A dropped message is the accepted outcome. A duplicate message is not.

There is no automatic reconciliation job and no same-day claim reset. Releasing a `rejected` claim is backlog, not this phase.

The mock gateway records the would-be message in the claim metadata needed for tests (recipient hash, template id, period). Its failure mode is a test constructor argument. Tool arguments cannot ask the mock to fail or to double-send.

### Privacy in logs

Structured logs go to stderr, because stdout is the stdio wire. A logging filter removes telephone numbers, message bodies, bearer tokens, and SMS passwords. Audit and claim rows store a telephone hash when a destination must be proven, not the raw number. Tool results omit the raw telephone number.

## 8. Security controls

- Tenant and scopes come from the verified token on HTTP, or from the single stdio binding.
- Workbook paths exist only in server config and must resolve under `workbook_roots`.
- Tool schemas exclude path, tenant, phone, message body, and secrets.
- Cross-tenant ids collapse to `not_found`.
- Writes require the current content hash, a lock, a backup, and atomic replace.
- SMS claims are unique and committed before dispatch. Provider calls are not retried.
- Opt-out is sticky. Suppression is checked before a claim insert.
- Live SMS requires both the provider value and the live-send flag.
- Workbook cells and SMS replies are data. They are not instructions to the server.
- Example tokens are labeled synthetic. The HTTP server logs a startup warning when those example hashes are loaded, and the default bind address remains loopback.
- `.env.example` lists names only. Real `.env`, data directories, backups, and databases are gitignored.
- Tests use invented people (`Synthetic Person A`) and fictional `+46700000001`-style numbers.

## 9. Persistence, migration, and rollback

Tables:

- `sms_claims` with the unique key in section 7 and a state column.
- `sms_suppressions` keyed by tenant and employee, with reason and active flag.
- `audit_events` append-only, each row storing `prev_hash` and `row_hash` so a test can detect a mutated prefix.

Alembic revisions live in `migrations/`. Forward migration is `alembic upgrade head`. Rollback of the schema is `alembic downgrade -1` for the single initial revision. Workbook bytes are unaffected by the database migration.

SQLite connection settings: WAL, `busy_timeout` 30 seconds, foreign keys on. Integrity errors on the claim key are turned into a read of the winning row.

Rollback of a bad write is the backup copy procedure in section 5. Application rollback is a git revert of the server plus the Alembic downgrade. No workbook format migration is embedded in customer files; the version is an external hash, not a hidden sheet.

## 10. Milestones

Each milestone is one implementation cycle after approval: code, tests, then `docs/status.md`. Stop and ask again if a milestone needs a different tool, store, or trust boundary.

### M0 — Skeleton

Create `pyproject.toml`, package `src/timereport_mcp/`, empty modules matching the layout, `tests/`, `.gitignore`, `.env.example`, and ruff/pyright configuration. Add a console script `timereport-mcp`.

Acceptance: a fresh environment installs the project; `ruff`, `pyright`, and `pytest` run; the smoke test imports the package; no tool is registered yet.

### M1 — Config and principals

Load tenants, clients, workbook allowlists, policy, templates, and calendar files. Implement principal resolution and scope checks without opening the network.

Acceptance tests: two tenants; token hash matches one client; unknown token yields no principal; stdio startup fails when `TIMEREPORT_STDIO_TENANT` is missing or unknown; a path outside `workbook_roots` fails config load; an `LLM`-prefixed environment key fails startup; example config loads synthetic tenants only.

### M2 — MCP shell and `list_workbooks`

Register the server with the v2 SDK. HTTP mode uses `TokenVerifier` and `AuthSettings`. `list_workbooks` returns the caller’s workbooks.

Acceptance tests: in-memory service test with an explicit principal; localhost HTTP test with two bearer tokens; tenant B’s token does not observe tenant A’s workbook id; missing token receives HTTP 401; a tool argument `tenant_id` is not part of the schema; stdout stays free of log lines when the stdio server handles a call.

### M3 — Read and validate workbooks

Implement `inspect_timesheet` and `read_timesheet_entries`. Generate synthetic workbooks in tests.

Acceptance tests: canonical sheet; missing column; ambiguous date text; blank hours; boolean hours; formula hours; duplicate keys; corrupt zip bytes; a second sheet left unread by the reporting columns; a workbook id from the other tenant; a direct path string is not an accepted argument. Row limit covered with a small injected limit in the test, not a 50 000-row file.

### M4 — Reporting

Implement `get_reporting_status` as a pure function over validated rows plus policy.

Acceptance tests:

- October 2026 full-time, no holidays, 176 expected.
- February 2026 full-time, 160 expected.
- One configured holiday subtracts 8.
- `none_confirmed` inside its range leaves weekdays at 8.
- Missing holiday setting returns `policy_review_required` and null expected hours.
- Part-time explicit weekday map.
- Part-time weekly total without a weekday map returns `policy_review_required`.
- Approved leave on a Wednesday subtracts that day.
- A blank cell is not treated as leave.
- Reported hours above baseline are `complete` with `hours_above_expected`.
- Invalid rows yield `data_review_required`.
- No test input is an SMS reply.

### M5 — Controlled update

Implement `update_timesheet_entry`, locks, backups, and audit hash chain.

Acceptance tests: version match writes the cell and preserves another sheet’s value; stale version does not change bytes; two concurrent writers with the same version produce one success and one `version_conflict`; lock timeout is observable; formula target is refused; `create_if_missing` false does not add a row; backup bytes equal the pre-image; a mutated audit row fails the hash-chain check; cross-tenant write is `not_found`.

### M6 — Mock SMS, claims, suppression

Add the database, migrations, mock gateway, `send_sms`, `get_sms_status`, and `set_sms_suppression`.

Acceptance tests:

- First send records one mock delivery.
- Sequential repeat, concurrent repeat, and a new process using the same database file do not create a second delivery.
- A pre-inserted `pending` row does not call the provider.
- Mock timeout path stores `uncertain` and a repeat stays `uncertain`.
- Mock 4xx path stores `rejected` and a repeat stays `rejected`.
- `opt_out` and `admin` suppression send nothing.
- Clearing `admin` allows a later send; clearing `opt_out` is rejected.
- Budget cap blocks the next employee without inserting a claim.
- Unparseable phone sends nothing.
- A second template on the same day replays the first claim.
- Log capture for a send contains neither the raw phone nor the template body nor the bearer token.
- Default provider in the test settings is mock.

### M7 — 46elks adapter, inert in acceptance

Implement the adapter and the startup gate. Tests stay on `MockTransport`.

Acceptance tests: built request uses Basic auth, `from`, `to`, `message`, and `dontlog=message`; success parses `id`; HTTP 400 maps to `rejected`; timeout maps to `uncertain`; `SMS_PROVIDER=elks` with `SMS_LIVE_SEND=false` refuses startup; credentials do not appear in the mapped error or log record. No test opens a socket to 46elks.

### M8 — Consumer reference and handoff

Write `docs/mcp_api.md` from the implemented schemas, with one synthetic example per tool. Update `docs/mcp_interface.md` so the tool table matches the code. Add `scripts/smoke_mcp_client.py` that invokes the tools against generated fixtures through `mcp.Client` and exits. Add `docs/handoff.md` with the Phase 1 checklist. Run the full test, lint, and type-check suite.

Acceptance: the smoke script prints workbook status and a mock SMS replay for two tenants; the checklist items are all true; the suite passes on this Windows host.

## 11. Risks

| Risk | Handling in this plan |
|---|---|
| SDK v2 APIs move after 2.3.0 | Pin `mcp>=2.3.0,<3`. Re-read the changelog at M2. Do not copy v1 `FastMCP` snippets. |
| stdio and the in-memory client have no HTTP identity | stdio binds one configured tenant. HTTP tests carry the cross-tenant proof. Service tests pass principals explicitly. |
| SQLite write lock is coarse under several processes | Acceptance target is one server process. PostgreSQL is the same schema when an operator sets `DATABASE_URL`. |
| 46elks has no idempotency key | Claim is committed first. Crashes become `pending` and block a resend. Document the dropped-message outcome in the API reference. |
| `openpyxl` cannot evaluate formulas | Formula hours are `formula_not_supported`. Evaluation is backlog. |
| Real customer sheets differ from the canonical headers | Unmapped headers fail `schema_mismatch`. Aliases are explicit config. |
| Holiday law is easy to over-encode | Expected hours require a configured calendar or `none_confirmed`. The repo’s calendars are synthetic. |
| Name-derived employee ids merge distinct people | Warning `employee_identity_is_name_derived`. Operators who need stable ids add the id column. |
| Windows replace and lock behavior | M5 runs on the project’s Windows host and asserts the surviving file bytes. |
| Prompt text inside a cell or an SMS reply | Templates are fixed. Cell text is never copied into an SMS or executed. |

## 12. Phase 2 exclusions

This repository will not contain:

- A runtime agent, system prompt, tool-choice policy, or `runtime/TIMEREPORT_AGENT.md`.
- An LLM client, model name, or LLM API key setting that the server loads.
- A cron entry, job runner, polling loop, or autonomous reminder process.
- Real employee spreadsheets, real telephone numbers, or live SMS credentials.
- A check frequency or a rule that keeps messaging until a human replies.
- Inbound SMS webhooks, delivery-callback endpoints, or a claim-release admin workflow.
- OneDrive or Google Sheets ingestion.
- A legal Swedish holiday or collective-agreement database presented as complete policy.

Phase 2 supplies its own workbooks, its own `.env` (SMS credentials and LLM credentials), and its own decision of when to call the tools. The integration surface from this repository is `docs/mcp_api.md`.

## 13. What approval authorizes

Approval authorizes milestones M0 through M8 in order, using synthetic data and the mock SMS provider only. It does not authorize a live 46elks send, a dependency outside section 2, or a change to the trust boundary (who chooses the tenant, who may pass a path, when a second SMS may be sent). Those changes require an updated plan and a new approval.
