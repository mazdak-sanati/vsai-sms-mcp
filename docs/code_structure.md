# Proposed Code Structure — MCP Server Only

**This is a target structure, not an instruction to generate code before plan approval.** The coding agent may refine module names during planning and document the reason.

```text
vectorsynapse-timereport-mcp-server/
├── AGENTS.md                    # coding-agent entry point ONLY
├── docs/
│   ├── README.md
│   ├── process.md
│   ├── code_structure.md
│   ├── tech_stack.md
│   ├── improvement.md
│   ├── mcp_interface.md          # external API contract, NOT agent instructions
│   ├── plan.md                   # coding agent creates in planning
│   └── status.md                 # coding agent creates/updates
├── src/timereport_mcp/
│   ├── server.py                 # MCP tool registration and transport
│   ├── config.py                 # trusted server-side config
│   ├── models.py                 # schema validation
│   ├── auth/                     # client identification, capabilities, tenancy
│   ├── excel/                    # safe workbook adapters, validation, controlled writes
│   ├── reporting/                # expected-hours computation and status
│   ├── sms/                      # SMS interface, mock and optional provider adapter
│   ├── persistence/              # idempotency, audits, suppression and migrations
│   └── observability/            # structured logs with PII redaction
├── tests/
│   ├── fixtures/                 # invented employee/workbook data
│   ├── unit/
│   ├── integration/
│   └── security/
├── config/                       # server-only configuration examples
├── scripts/                      # local setup/tests, no reminder scheduler
├── pyproject.toml
├── .env.example                  # placeholders only, never real keys
└── .gitignore
```

## Module boundaries
- `server`: declare tools and return typed results. No policy logic or direct filesystem access inside handlers.
- `auth`: resolve tenant from authenticated transport, not tool-provided tenant IDs. Restrict workbook identifiers to paths authorized per tenant.
- `excel`: read a validated `.xlsx` from the allowlist and implement purpose-built writes with locking/version checks, audit trail, backups and atomic replacement. Do not expose arbitrary raw cell evaluation or broad path traversal.
- `reporting`: pure deterministic calculation of expected vs reported hours; status can be complete, incomplete or policy-review-needed. Default Swedish full-time policy 40h/week, with approved exceptions.
- `sms`: provider-independent send contract and fake gateway. Expose on-demand send, never self-trigger. Optional 46elks adapter can be wired for future deployment without live testing.
- `persistence`: server-side state for daily send uniqueness, opt-outs/suppression and audit events. No agent memory or LLM state.

## Cross-tenant authorization
Server owns the tenant registry, authorized workbook mappings, SMS capability, sending limits and provider credentials. A client cannot escalate permissions by changing a workbook path, tenant ID, employee ID or message text. For multi-user/remote access, use authenticated MCP transport and per-client capability checks.

## Future Phase 2 integration (boundary only)
Another project will supply its own agent, actual Excel files, checks cadence, `.env` secrets (SMS key and LLM key) and decision logic. This repository must **not** contain `runtime/TIMEREPORT_AGENT.md`, prompts, workflows or scheduler implementation. `docs/mcp_interface.md` is the only client integration contract needed here.
