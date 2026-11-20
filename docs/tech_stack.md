# Technology Stack — Phase 1 MCP Server

**Status:** Proposed list, validated 2026-11-19. Final choices, including the SQLite acceptance default and the MCP SDK v2 (`MCPServer`) decision, are recorded in `docs/plan.md`. This file stays the original candidate list.

## Proposed stack
| Concern | Candidate | Why / Phase 1 requirement |
|---|---|---|
| Language | Python 3.12+ | Mature ecosystem for MCP + Excel |
| MCP | Official Python MCP SDK | Expose typed tools; verify SDK version/transport/auth API |
| Workbook | `openpyxl` | `.xlsx` reads and controlled writes; preserve source integrity |
| Tool validation | Pydantic | Typed input/output validation |
| State store | PostgreSQL | Durable multi-tenant SMS idempotency, suppressions, audit |
| Persistence | SQLAlchemy + Alembic | Transactions, migrations and uniqueness constraints |
| SMS | `SmsGateway` protocol + mock; optional **46elks** adapter | Tests without keys; interchangeable provider for future deployment |
| HTTP | `httpx` | Timeouts, bounded requests; verify error/idempotency semantics |
| Tests | `pytest`, fakes, concurrency/integration tests | No real SMS or private spreadsheets |
| Environment | `pyproject.toml`, `.env.example` with placeholders | Reproducible, no committed secrets |
| Observability | Structured privacy-redacted logs | Safe troubleshooting and tenant audits |

## What is not in the MCP stack
- **No LLM framework or LLM SDK.** The MCP server exposes tools; Phase 2's separate agent project will choose its model and manage the LLM API key.
- **No task scheduler or autonomous reminder service.** Phase 2 chooses when to call MCP tools and how frequently to check workbooks.
- **No runtime agent config Markdown, prompt files or orchestration framework.** Only document the server's tool API.
- No OneDrive/Google Sheets ingestion in this initial local-filesystem scope.

## SMS interface and deployment
46elks is the leading initial Swedish SMS adapter candidate; keep provider selection replaceable. The Phase 1 server must support a fake provider for integration tests and may implement a credential-dependent provider adapter, but **must not require or use real SMS API keys for Phase 1 acceptance**. Do not send live SMS in development tests. Real sender identity, pricing, two-way numbers, opt-out rules and delivery callbacks need validation before production enablement.

An eventual deployment may inject an SMS API key through an environment variable or secure secret manager. The *future agent project* will contain its `.env` with SMS API key and LLM API key; the MCP server must never attempt to use or store the LLM API key. Avoid copying the same secrets into two repositories. Define how credentials are made available to the SMS adapter at deployment without exposing them to MCP tool outputs or logs.

## Policy / reporting behavior
Source Excel includes employee name, phone number, date and hours. Missing hours are judged against server-configured approved expected hours. Sweden default ordinary full-time baseline: **40 hours/week**, commonly 8h per Monday–Friday workday. Monthly totals vary with scheduled workdays, holidays and agreed exceptions. If a required policy input is missing, return `policy_review_required` instead of asserting a missing timesheet.

## Multi-tenant deployment and transport
Plan and document a concrete mode (local stdio or authenticated streamable HTTP) that preserves strong isolation for independent organizations. Server-side workbook allowlists and trusted tenant attribution are mandatory. SMS send caps and opt-outs are enforced centrally even with repeated tool invocations.

## Items coding agent must decide during planning
- Transport and authorization mechanism; tenant identity, tenancy model and configuration provenance.
- Stable workbook and employee identifiers, schema mapping and error shapes.
- Excel locking and atomic replace semantics for supported OS/deployment.
- Database lifecycle and migrations, idempotency uniqueness, provider uncertainty.
- Provider activation control, audit/privacy retention and supported deployments.
- Scope-limited acceptance tests using fake SMS and generated workbooks.
