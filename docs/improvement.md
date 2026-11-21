# Improvement Backlog — MCP Server

Purpose: record ideas **for the MCP server itself**. This is not an agent product roadmap. Do not implement backlog items without explicit plan approval.

## Phase 1 quality / architecture
| Priority | Improvement | Evidence / acceptance |
|---|---|---|
| P0 | Tenant authorization and path allowlist | Cross-tenant read/write/send requests denied |
| P0 | Reliable daily send idempotency | Concurrent/retried calls cannot duplicate a daily send |
| P0 | Safe Excel changes | Version conflicts, corruption, backup/recovery tests |
| P0 | Mock SMS provider with delivery simulation | No network/SMS credentials in tests |
| P0 | Clear tool errors and schema validation | Deterministic machine-readable results |
| P1 | Workbook mapping flexibility | Configurable known column names, no guessing of identities |
| P1 | Expected-hours policy tests | 40-hour baseline plus holidays/leave/part-time exceptions |
| P1 | Per-tenant operational audit | Redacted and tamper-evident enough for incident review |
| P1 | Privacy/retention controls | No secrets/PII in ordinary logs |
| P2 | Additional SMS gateway adapters | Provider-independent contract tests |
| P2 | Richer provider delivery status handling | Observability without duplicate sending |

## Explicitly deferred to Phase 2 (separate project)
- Runtime AI agent development, system prompts or agent-specific Markdown configuration.
- LLM provider selection, prompt templates, memory/RAG, tool-choice reasoning.
- Runtime `.env` with real Excel paths, SMS API keys and LLM API keys.
- Choosing an automated checking frequency and implementing scheduled runs.
- The policy of repeatedly checking/reporting/reminding until completion.

These future-system concerns may motivate generic MCP capabilities, but **must not become Phase 1 deliverables**. The MCP server remains reactive to external clients.

## Recorded during 2026-11-19 planning

These are not part of the approved milestone list until a later plan revision says so.

| Priority | Improvement | Classification |
|---|---|---|
| P0 | Simplify `send_sms` to a pass-through send (no daily claim blocking); agent decides send policy; credentials remain env-only | Phase 1 product change — needs plan revision + approval before code change |
| P2 | PostgreSQL CI job when `TEST_DATABASE_URL` is set | Phase 1 later. Acceptance uses SQLite. |
| P2 | Evaluate workbook formulas | Phase 1 later. `openpyxl` does not evaluate; formula hours are errors. |
| P2 | Tool to restore a workbook from a server backup | Phase 1 later. Recovery is an operator file copy. |
| P2 | Audited release of a `rejected` SMS claim on the same local day | Phase 1 later. Omitted so the daily key stays single-use. |
| P2 | Inbound STOP webhook and 46elks delivery callback | Phase 1 later as server API only. Not a reminder agent. |
| — | Maintained legal Swedish holiday and collective-agreement tables | Phase 2 data, supplied as operator policy. This repo ships synthetic calendars. |

## How to record a new improvement
Describe the observed issue, user benefit, affected MCP module/tool, proposed test, risk/priority and Phase 1 vs Phase 2 classification. Update `docs/status.md` if an improvement affects an approved milestone; otherwise keep it in this backlog.
