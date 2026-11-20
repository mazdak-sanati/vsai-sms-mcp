# AGENTS.md — Phase 1 MCP Server Coding Agent Entry Point

## Mission and hard scope boundary
Build **only** `vectorsynapse-timereport-mcp-server`: a reusable, secure Model Context Protocol (MCP) server providing tools for working with authorized local Excel timesheets and an SMS gateway. **Phase 1 does not build, configure, host, schedule, or operate a timesheet-follow-up AI agent.** The agent that will eventually use these MCP tools is **Phase 2, a separate project**.

The word *agent* in this file refers exclusively to the **coding agent** that is developing this MCP server, except when describing the eventual external MCP client as an integration boundary.

## Phase 1 deliverables
- MCP server implementation and packaging, callable tools and documented tool schemas.
- Secure multi-tenant boundaries and tenant-scoped local workbook access.
- Excel reading, validation, expected-hour comparison, and controlled update tools.
- SMS gateway interface, safe send tool with durable idempotency and a mock provider; design an optional 46elks integration that can be activated later with credentials.
- Automated tests, simulated workbook data, operational documentation, and a consumer-facing **MCP API reference**, not an agent prompt.
- No requirement for real Excel files, real phone numbers, SMS credentials, LLM credentials, or a runtime-agent `.env` to develop or test Phase 1.

## Explicitly out of scope (Phase 2)
- Creating any runtime agent, LLM prompt, chain, workflow, orchestration, or autonomous decision-maker.
- Writing a runtime agent Markdown configuration (`TIMEREPORT_AGENT.md`) or defining the agent's check frequency.
- Implementing daily checks, jobs, reminders scheduler, cron, polling loop or autonomous SMS triggers.
- Providing real spreadsheets, phone numbers, SMS API keys or LLM API keys in the Phase 1 repository.
- Implementing LLM calls or selecting an LLM vendor/model for the server.
- Automatically sending live SMS; sending requires an authenticated external tool invocation and explicit enablement.

**Future integration boundary:** Phase 2 will provide its own local Excel files, runtime configuration, and `.env` containing SMS API credentials and LLM API credentials (and any other runtime secrets). The MCP server never needs an LLM key; its SMS adapter may receive an SMS credential from the deployment environment when the separate Phase 2 system connects.

## Mandatory reading order
1. `docs/process.md`
2. `docs/code_structure.md`
3. `docs/tech_stack.md`
4. `docs/improvement.md`
5. `docs/mcp_interface.md`
6. `docs/status.md` and `docs/plan.md` when present
7. Existing source/tests, then current MCP SDK documentation during planning

## Development workflow: plan before coding
**On initial invocation, PLAN ONLY.** Inspect the repository and requirements, validate the technology choices, identify risks, and write `docs/plan.md` with small milestones, testable acceptance criteria, and Phase 1 exclusions. Create/update `docs/status.md` with a dated progress entry. No production implementation, dependency installation, real SMS, or external state changes before explicit approval.

Only an explicit user instruction such as **"Implement the approved docs/plan.md"** authorizes implementation. Execute the approved plan incrementally, test each step, and request approval again for material scope changes.

## Confirmed MCP server requirements
- Inputs are local `.xlsx` timesheets with employee name, telephone, date and reported hours; inspect and validate their actual schema.
- Compare reported hours with expected hours; standard Swedish full-time baseline is **40 hours/week** (conventional five-day schedule = 8 hours/day), subject to approved part-time schedules, holidays, leave, sick time and collective-agreement exceptions. Never infer missing policy as fact; return `policy_review_required` when incomplete.
- Support multiple organizations with server-enforced tenant isolation. No arbitrary client-provided absolute file paths or client-declared tenant identity.
- Offer purpose-specific, validated Excel reads/writes without broad filesystem permissions; protect concurrent edits with version checking, backups, locks and atomic replacement.
- Expose SMS sending as a **tool**, not a background process. The server must enforce at-most-once automated send per tenant/employee/reporting period/local calendar day using persistent idempotency. Ordinary employee responses do not prove timesheet completion; completion is determined from verified Excel data. Opt-outs and administrative suppression always prevent sending.
- Provide a replaceable SMS provider abstraction (46elks as initial candidate), fake/test provider, explicit live-send controls and privacy-safe logs.
- Never place real secret values or personnel data in git or generated documentation.

## Documentation discipline
After each meaningful planning or implementation step, update `docs/status.md` with timestamp, work done, evidence/tests, blockers, decisions and next actions. Track proposed enhancements in `docs/improvement.md`. `docs/plan.md` and `docs/status.md` belong under `docs/`, not at repository root.

## Definition of done for Phase 1
A separate client can discover and call the documented MCP tools against synthetic sample data and a mock SMS provider; tenant boundaries, workbook writes, validation, policy exceptions and message deduplication are tested. The system does **not** require a built runtime AI agent, scheduled execution, real SMS account or LLM API key for acceptance.
