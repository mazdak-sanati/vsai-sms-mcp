# Process — Phase 1 MCP Server Development

## Purpose
This document defines the **coding agent's development process for the MCP server only**. Do not implement or plan the future timesheet-follow-up runtime agent.

## Development cycle
1. **Read** root `AGENTS.md`, all docs named there, and inspect the repository.
2. **Analyze Phase 1** tool responsibilities, workbook contract, tenant auth, SMS adapter and fault handling; identify any unsupported assumptions.
3. **Select stack**: validate `docs/tech_stack.md` against the current MCP SDK; document the chosen architecture and why.
4. **Write `docs/plan.md`**: milestones, test criteria, risks, security controls, migration/rollback and deliberate Phase 2 exclusions.
5. **Update `docs/status.md`**: timestamp (Europe/Stockholm), current phase, completed planning, decisions, blockers and next action.
6. **Stop** and wait for explicit approval. Planning does not authorize coding.
7. **After approval**, implement one small plan milestone at a time, run tests, review results, update `docs/status.md` and continue.
8. **Finish** with documented MCP tool examples, tests and a Phase 1 handoff checklist. Do not create Phase 2 files.

## Required `docs/status.md` contents
Maintain a short current snapshot (phase, milestone, state, next action, blockers) and an append-only chronology. For every substantive planning or coding cycle record:
- timestamp and milestone
- changes made (file paths)
- tests executed and their result, or reason tests did not run
- decisions, risks, unresolved issues
- next concrete step and whether approval is pending

Never silently omit or overwrite the historical entries. When blocked, explain exactly what is missing and the safest next step.

## Implementation quality gates
- Type checking/linting, automated unit/integration tests, dependency review.
- Strict schemas and bounded inputs for all MCP tools; no prompt instructions from workbook cells or messages.
- Verify isolation across at least two tenants, including unauthorized path guesses and cross-tenant references.
- Verify Excel sheet/column validation, date parsing, numeric handling, formula handling, corruption and read/write race conditions.
- Verify 40-hour weekly policy and variable monthly hours, partial schedules, holidays and leave; uncertainty is returned as review-needed, not fabricated hours.
- Verify SMS request deduplication despite retries/concurrency/restarts; test opt-outs, budgets, telephone formatting and provider failure modes.
- Verify safe defaults: mock SMS, no live sends during tests, no LLM dependency, no background checks/scheduler.
- Validate operation through a generic MCP test client. No specialized runtime agent required.

## Operational boundaries
The MCP service must be request-driven; external clients decide if/when to request tools. Do not add an independent daily timer or retry loop that sends daily reminder messages. Internal bounded transport retries may be considered only with provider-safe idempotency and a documented uncertainty policy; they are not a scheduling subsystem.

## Secret and privacy handling
Use only `.env.example` with placeholder configuration names for local server testing. Never include real keys, real employee phones, private Excel content, or LLM secrets in this project. Phase 2 is responsible for furnishing its own `.env` with SMS and LLM API credentials. The MCP server should only consume the relevant SMS credential if/when an explicitly authorized integration is deployed. Test with fakes and synthetic data.

## Change control
If a proposed change expands beyond MCP functionality (agent orchestration, scheduling, prompts, LLM integration, runtime Markdown agent files), classify it as **Phase 2**, add a handoff note and do not implement it. Material Phase 1 scope/architecture changes require an updated `docs/plan.md` and fresh user approval.
