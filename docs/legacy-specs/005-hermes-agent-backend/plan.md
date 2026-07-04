# Implementation Plan: Hermes Agent Backend Integration

**Branch**: `005-hermes-agent-backend` | **Date**: 2026-06-11 | **Spec**: [spec.md](spec.md)

## Summary

Make the Hermes Agent backend real. Replace the imagined `/v1/tasks/sse` global
feed with Hermes Agent's actual async runs API (`POST /v1/runs` + per-run SSE
event streams), introduce a `DeliveryCoordinator` as the single owner of
proactive speech, implement context hydration from Hermes' built-in memory files
(`USER.md`/`MEMORY.md`) via a config-selected `file://` or `ssh://` source, and
document/script a fresh-machine Hermes bootstrap with zero external services
(Honcho stays a Hermes-side opt-in). Four new/reworked modules in
`voice_adapter/` plus targeted changes to `adapter.py`, `config.py`, and the
existing `HermesClient`.

## Technical Context

**Language/Version**: Python 3.11+ (runs in repo `.venv`, Python 3.14)

**Primary Dependencies**: existing — `pipecat-ai[websocket]`, `httpx`,
`watchdog`, `faster-whisper`, FastAPI/uvicorn; **new** — `asyncssh` (SFTP
context source)

**Storage**: in-memory task registry + delivery queue; JSON task journal at
`~/.voice_adapter/tasks.json`; Hermes-side persistence is Hermes' own
(`~/.hermes/` files + SQLite) and out of scope

**Testing**: `pytest` unit tests per module (mocked HTTP/SSE/SFTP); one
contract test runnable against a live Hermes server (skipped when unreachable)

**Target Platform**: Linux x86_64; Hermes co-located or remote over Tailscale
(reference: `jarlaxle`)

**Project Type**: Real-time voice pipeline server + remote agent backend

**Performance Goals**: dispatch handler returns < 100 ms after tool call (the
model's verbal handoff is never blocked); result spoken
< 2 s after completion (idle channel); context block adds < 50 ms to prompt
assembly; local loop latency unchanged with ≥ 2 runs in flight

**Constraints**: Tailscale-only cross-machine traffic; no cloud services; local
LLM never sees Hermes tool schemas; single voice session, multiple background
runs; fresh machine must work with zero external services

**Scale/Scope**: single user; ≤ ~5 concurrent runs; 1200-token context budget

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Hardware Agnosticism | ✅ PASS | Topology (local/remote Hermes) is pure config; no hardcoded hosts — jarlaxle appears only in `.env.example` comments. |
| II. Configuration-Driven | ✅ PASS | All new knobs in `AdapterConfig` + `.env.example`; `HERMES_CONTEXT_SOURCE` URI selects source implementation. |
| III. Modular Architecture | ✅ PASS | `hermes_run_client`, `task_broker` (bridge), `delivery`, `context_source` are independently importable/testable. |
| IV. Async-First I/O | ✅ PASS | httpx async SSE, asyncssh SFTP, asyncio task pool; journal writes are tiny and atomic. |
| V. Documentation Discipline | ✅ PASS | spec/plan/research/contracts/quickstart precede code; consumed API pinned in contracts/hermes-api.md. |
| VI. Resilient Error Handling | ✅ PASS | Capabilities-probe fallback, per-stream backoff + state reconciliation, stale-cache-over-failure hydration, journal recovery. |

## Architecture

```
                         ┌────────────────────────────── voice machine ─┐
 satellite.py ──WS──► voice_adapter (Pipecat pipeline)                  │
                        │  AdapterProcessor                             │
                        │   ├─ system prompt ◄── PreFetchCache.get_context()
                        │   │                      ▲                    │
                        │   │            ContextSource (file:// │ ssh://)
                        │   │                      ▲                    │
                        │   ├─ tools: query_hermes_agent, cancel_task   │
                        │   ▼                                           │
                        │  TaskBroker ──POST /v1/runs──────────────┐    │
                        │   │  HermesTask registry + TaskJournal   │    │
                        │   │  per-run SSE: GET /v1/runs/{id}/events    │
                        │   ▼                                      │    │
                        │  DeliveryCoordinator ──► TTS (idle-gated)│    │
                        └──────────────────────────────────────────┼────┘
                                                            Tailscale
                                                                   ▼
                                            hermes-agent gateway (api_server)
                                            127.0.0.1↔host:8642, bearer auth
                                            tools · skills · memory (MEMORY.md,
                                            USER.md, state.db) under ~/.hermes/
```

### Module Changes

#### 1. `voice_adapter/hermes_run_client.py` (new)

`HermesRunClient` — the only component that speaks HTTP to Hermes:

- `probe_capabilities() -> Capabilities` (startup; cached; lazy re-probe on
  dispatch if the probe failed)
- `start_run(prompt, *, session_id) -> RunHandle` (`POST /v1/runs`, 202)
- `stream_events(run_id) -> AsyncIterator[RunEvent]` (SSE with per-stream
  exponential backoff 1s→60s; 30s server keepalives expected)
- `get_run(run_id) -> RunState` (reconciliation after stream gaps/restart)
- `stop_run(run_id)`, `resolve_approval(run_id, decision)`
- `chat_fallback(prompt, *, session_id) -> AsyncIterator[str]` — delegates to
  the existing `voice_satellite.gateway.hermes_client.HermesClient` streaming
  path (whose URL handling is fixed: `{HERMES_BASE_URL}/chat/completions` with
  `/v1` in the base)
- Headers on every call: `Authorization: Bearer`, `X-Hermes-Session-Key`
  (stable), `X-Hermes-Session-Id` (per voice session)

#### 2. `voice_adapter/task_broker.py` (new — supersedes the planned `pipecat_bridge.py`)

`TaskBroker` — owns the `HermesTask` registry and run lifecycles:

- `dispatch(request_text, session_id) -> HermesTask`: create task (`pending`),
  `start_run`, flip to `executing`, spawn one consumer task per run that drains
  `stream_events` and applies the idempotent state machine
- terminal events → build `ProactiveResult` → hand to `DeliveryCoordinator`
  (results for `cancelled` tasks are dropped here)
- `cancel(task_id)`: honors `TranscriptManager.can_cancel()`; calls `stop_run`
- `TaskJournal`: persist non-terminal tasks + undelivered results on every
  transition (atomic `os.replace`); `restore()` on boot re-subscribes
  `executing` runs and reconciles via `get_run`
- Fallback mode (no runs API): wraps `chat_fallback` in the same task/registry
  semantics — buffer stream, synthesize a single completion event

#### 3. `voice_adapter/delivery.py` (new)

`DeliveryCoordinator` — single owner of system-initiated speech:

- FIFO queue of `ProactiveResult`; `notify_*` hooks from pipeline frames
  (`UserStartedSpeaking`, `BotStoppedSpeaking`, TTS activity) maintain
  channel-idle state
- When idle: dequeue one result → preamble + speech text → inject via the
  (existing) `AdapterProcessor.inject_text` path → mark delivered → journal
- Condense results over `RESULT_SPEECH_BUDGET` via one local-LLM call; full
  text still committed to the transcript turn
- Holds undelivered results across session teardown; on next session start,
  announces backlog after the wakeword ack
- Barge-in during delivery: stop, commit `[interrupted]`, do not re-queue

#### 4. `voice_adapter/context_source.py` (new) + `pre_fetch_cache.py` (rework)

- `ContextSource` ABC: `async fetch() -> dict[str, FileSnapshot]`,
  `watch(callback)` (optional), `health`
- `LocalFileSource` — watchdog observer on `<path>/{USER.md,MEMORY.md}`;
  polling fallback if inotify unavailable
- `SshFileSource` — asyncssh SFTP; stat-first (mtime/size) then fetch changed;
  poll every `HERMES_CONTEXT_POLL_INTERVAL`; failure ⇒ stale snapshot + warning
- `HonchoSource` (optional, FR-020) — only if `HONCHO_API_URL` set
- `PreFetchCache` becomes real: merges sources into `ContextSnapshot`,
  `is_warm` after first successful (or first failed-but-attempted) fetch,
  `get_context()` lock-guarded in-memory read; `build_prompt_block(budget)`
  implements the priority order USER.md → task summary → MEMORY.md with
  section-boundary truncation
- `parse_context_source(uri)` → source instance (`file://`, `ssh://`, `none`)

#### 5. `voice_adapter/adapter.py` (modify)

- System prompt = static instructions + `PreFetchCache.build_prompt_block()`
  (rebuilt per turn, < 1 ms read)
- Tool schemas: keep `query_hermes_agent`; add `cancel_task` and answer status
  questions from `get_context_for_prompt()` task tags (no status tool needed)
- `handle_query_hermes` → `TaskBroker.dispatch()` (non-blocking; the model's
  verbal handoff is the dispatch acknowledgement — no filler clip by design);
  remove the inline `consume_hermes`/`inject_text` direct path
- Wire broker/coordinator/cache into FastAPI lifespan (startup: journal
  restore, capabilities probe, hydration warm-up; shutdown: journal flush,
  source stop)
- `TeardownInterceptor` notifies `DeliveryCoordinator` of session end (backlog
  retention) — termination logic itself unchanged

#### 6. `voice_adapter/config.py` (modify)

Add: `hermes_session_key`, `hermes_context_source` (URI),
`hermes_context_poll_interval`, `context_token_budget`,
`result_speech_budget`, `task_journal_path`. Remove: `hermes_sse_url`,
`hermes_memory_path` (superseded by the URI). `honcho_api_url` default becomes
empty (disabled). Update `.env.example`.

#### 7. `scripts/setup_hermes.sh` + `scripts/check_hermes.py` (new)

Bootstrap helper (official installer → enable api_server adapter → set
`API_SERVER_KEY` → start gateway) and a validation probe (`/health`,
`/v1/capabilities`, auth check, one round-trip run) used by quickstart and by
the Phase-0 contract test.

### Removed / Superseded from 004

- `pipecat_bridge.py` (never built) → `task_broker.py` + `delivery.py`
- `HERMES_SSE_URL` / global `/v1/tasks/sse` listener → per-run event streams
- `PreFetchCache` no-op stub → real implementation over `ContextSource`
- Direct `inject_text` from background Hermes consumption → all proactive
  speech through `DeliveryCoordinator`

## Phase Outline

- **Phase 0 — Contract pinning**: deploy Hermes (dev), run `check_hermes.py`,
  record verified event names/payloads in `contracts/hermes-api.md` (resolves
  OQ-1..3 from research.md). Gate: contract test green against live server.
- **Phase 1 — Run client + broker (US1 MVP)**: `HermesRunClient`, `TaskBroker`
  (no journal yet), minimal delivery (idle-gated single queue), adapter wiring.
  Gate: US1 independent test passes end-to-end by voice.
- **Phase 2 — Delivery hardening (US2)**: multi-task ordering, buffering,
  barge-in semantics, condensation.
- **Phase 3 — Bootstrap & docs (US3)**: setup script, quickstart both
  topologies, auth warnings.
- **Phase 4 — Context hydration (US4)**: sources, cache, prompt block, warm
  gate.
- **Phase 5 — Lifecycle control (US5)**: cancel tool, status-from-tags prompt
  guidance, approvals.
- **Phase 6 — Persistence (US6)**: journal, restart recovery, next-session
  backlog announcements.
- **Phase 7 — Polish**: soak test, README/AGENTS updates, `.env.example`,
  latency validation against SC-001..009.

Detailed task breakdown: [tasks.md](tasks.md).

## Risks

| Risk | Mitigation |
|---|---|
| Upstream runs-API schema drift (it's recent) | Contract test + pinned `contracts/hermes-api.md`; capabilities probe + chat fallback keeps MVP usable even if runs break |
| Run results too verbose for speech | FR-012 condensation path; budget configurable |
| SFTP polling latency (30s) makes context feel stale | Acceptable: MEMORY.md/USER.md change on Hermes' curation cadence (minutes+), not per-turn |
| asyncssh host-key/agent issues on headless boxes | Quickstart documents `known_hosts` pre-seeding; source failure degrades to stale cache, never blocks |
| Local model misuses cancel/status tools | Few-shot guidance in system prompt; `can_cancel()` guard server-side of the tool |
