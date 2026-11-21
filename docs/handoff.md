# Phase 1 Handoff Checklist

- [x] MCP server package installable via `pip install -e ".[dev]"`
- [x] Tools discoverable and callable through `mcp.Client` against synthetic fixtures
- [x] Tenant isolation enforced (cross-tenant workbook ids return `not_found`)
- [x] Excel inspect/read/update with version check, lock, backup, atomic replace
- [x] Reporting: 40h/week baseline, holidays/leave/part-time, `policy_review_required`
- [x] SMS mock provider with durable daily idempotency and suppression
- [x] Optional 46elks adapter tested with `httpx.MockTransport` only (no live sends)
- [x] Consumer API reference: `docs/mcp_api.md` (agent/client usage guide)
- [x] MCP server `instructions` + tool descriptions aligned with `docs/mcp_api.md`
- [x] No runtime agent, scheduler, LLM SDK, or real credentials in this repository

## How a Phase 2 agent / client connects

1. Read **`docs/mcp_api.md`** before wiring the agent.
2. Provide real workbooks under configured `workbook_roots` and a tenant TOML (see `config/tenants.example.toml`).
3. Set `SMS_PROVIDER=mock` for dry runs, or `SMS_PROVIDER=elks` with `SMS_LIVE_SEND=true` and `ELKS_*` in the **deployment** environment (not this git repo).
4. Put LLM credentials only in the Phase 2 agent process — never in this MCP server.
5. Connect via stdio (`TIMEREPORT_STDIO_TENANT=...`) or Streamable HTTP with a bearer token.
6. Call tools on a schedule of the agent’s choosing; this server never self-triggers.
7. Treat `send_sms` results with `replayed: true` as “already sent today for that claim key,” not as a new delivery.
