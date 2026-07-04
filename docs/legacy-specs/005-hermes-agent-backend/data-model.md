# Data Model: Hermes Agent Backend Integration

**Branch**: `005-hermes-agent-backend` | **Spec**: [spec.md](spec.md)

## HermesTask (extended — `voice_adapter/transcript_manager.py`)

Existing entity, extended. One per dispatched background request.

| Field | Type | Notes |
|---|---|---|
| `task_id` | `str` | Existing human-readable alias `task_YYYYMMDD_HHMMSS_XX` |
| `run_id` | `str \| None` | Server-issued by `POST /v1/runs`; `None` until accepted and in chat-fallback mode |
| `state` | `HermesTaskState` | `pending → executing → completed \| failed \| cancelled` (adds `FAILED` to the existing enum) |
| `request_text` | `str` | The user request sent to Hermes; source of the delivery preamble |
| `result_text` | `str \| None` | Full result (terminal states) |
| `session_id` | `str` | Voice session (`X-Hermes-Session-Id`) that dispatched it |
| `created_at` / `updated_at` | `datetime` | Local clock |
| `delivered` | `bool` | Result spoken (or spoken-interrupted) |

**State machine** (idempotent — events for terminal tasks are ignored):

```
pending ──accepted──► executing ──completed──► completed ──spoken──► delivered
   │                     │   │──failed──────► failed     ──spoken──► delivered
   │                     └────stop/user─────► cancelled  (result discarded)
   └──dispatch error────► failed
```

Transitions out of `pending`/`executing` only; duplicate or out-of-order events
log and no-op.

## RunEvent (`voice_adapter/hermes_run_client.py`)

Parsed SSE event from `GET /v1/runs/{run_id}/events`.

| Field | Type | Notes |
|---|---|---|
| `run_id` | `str` | Stream is per-run; carried for safety/logging |
| `kind` | `RunEventKind` | `ACCEPTED`, `PROGRESS`, `APPROVAL_REQUIRED`, `COMPLETED`, `FAILED`, `KEEPALIVE`, `UNKNOWN` |
| `payload` | `dict` | Raw event data; `COMPLETED.payload` carries result text |
| `raw` | `str` | Original SSE data line (debugging/contract drift detection) |

Exact upstream event names are pinned during Phase 0 (see
[contracts/hermes-api.md](contracts/hermes-api.md)); `UNKNOWN` kinds are logged
and skipped, never fatal.

## RunState / Capabilities (`hermes_run_client.py`)

- `RunState`: snapshot from `GET /v1/runs/{run_id}` — `run_id`, `status`,
  `result_text?`, used for reconciliation after stream gaps and restarts.
- `Capabilities`: from `GET /v1/capabilities` — minimally `supports_runs: bool`,
  `model_name`, `raw: dict`. Probe failure ⇒ `supports_runs=False` (fallback
  mode) with lazy re-probe.

## ProactiveResult (`voice_adapter/delivery.py`)

One queued deliverable. Created only by `TaskBroker` (results, failures) and the
approvals path.

| Field | Type | Notes |
|---|---|---|
| `task_id` | `str` | Back-reference |
| `kind` | `DeliveryKind` | `RESULT`, `FAILURE_NOTICE`, `APPROVAL_REQUEST`, `BACKLOG_ANNOUNCEMENT` |
| `preamble` | `str` | Re-engagement phrase derived from `request_text` ("About your schedule — ") |
| `speech_text` | `str` | Possibly condensed (≤ `RESULT_SPEECH_BUDGET`) |
| `full_text` | `str` | Uncondensed; committed to transcript |
| `enqueued_at` | `datetime` | FIFO ordering |
| `state` | `DeliveryState` | `QUEUED → SPEAKING → DELIVERED \| INTERRUPTED` |

**Delivery gate** (all must hold): no user speech active, no bot speech active,
no TTS in flight, session active (else retain for next session).

## ContextSource (`voice_adapter/context_source.py`)

ABC with implementations selected by `parse_context_source(uri)`:

| Impl | URI | Refresh | Failure mode |
|---|---|---|---|
| `LocalFileSource` | `file:///home/u/.hermes` | watchdog/inotify, ≤2 s (polling fallback) | stale snapshot + warning |
| `SshFileSource` | `ssh://aiden@jarlaxle/home/aiden/.hermes` | SFTP poll, `HERMES_CONTEXT_POLL_INTERVAL` (30 s), stat-guarded | stale snapshot + warning |
| `NullSource` | `none` | — | always-empty snapshot, `is_warm` immediately |
| `HonchoSource` (optional) | from `HONCHO_API_URL` | HTTP poll, `HONCHO_POLL_INTERVAL` (25 s) | stale snapshot + warning |

`FileSnapshot`: `path`, `content: str`, `mtime`, `size`, `fetched_at`.
Files consumed: `USER.md`, `MEMORY.md` (missing files ⇒ empty content, one
startup warning).

## ContextSnapshot (extended — `voice_adapter/pre_fetch_cache.py`)

| Field | Type | Notes |
|---|---|---|
| `user_profile` | `str` | `USER.md` content |
| `agent_memory` | `str` | `MEMORY.md` content |
| `recent_memories` | `str` | Optional Honcho enrichment (empty if disabled) |
| `pending_tasks` | `list[TaskTag]` | Live, generated from registry each read |
| `last_updated` | `datetime` | Last successful source fetch |
| `source_health` | `dict[str, bool]` | Per-source reachability for logs/`/health` |

`build_prompt_block(budget=CONTEXT_TOKEN_BUDGET)` — priority order
`user_profile` → `pending_tasks` → `agent_memory`(→ `recent_memories`),
truncated at markdown section boundaries, ~4 chars/token heuristic.

## TaskJournal (`voice_adapter/task_broker.py`)

JSON file at `TASK_JOURNAL_PATH` (default `~/.voice_adapter/tasks.json`),
written atomically (`tempfile` + `os.replace`) on every task/delivery state
transition. Contents: schema version, non-terminal `HermesTask`s, undelivered
`ProactiveResult`s. On boot: `restore()` → re-subscribe `executing` runs,
reconcile via `GET /v1/runs/{run_id}`, re-queue undelivered results. Corrupt
file ⇒ warn, archive aside, start fresh (same pattern as 004 offline queue).

## Configuration additions (`voice_adapter/config.py`)

| Field | Env var | Default | Notes |
|---|---|---|---|
| `hermes_base_url` | `HERMES_BASE_URL` | `http://localhost:8642/v1` | existing; remote: `http://jarlaxle:8642/v1` |
| `hermes_api_key` | `HERMES_API_KEY` | — | existing; maps to Hermes `API_SERVER_KEY` |
| `hermes_session_key` | `HERMES_SESSION_KEY` | `voice-satellite` | stable long-term-memory scope (`X-Hermes-Session-Key`) |
| `hermes_context_source` | `HERMES_CONTEXT_SOURCE` | `none` | `file://…` \| `ssh://…` \| `none` |
| `hermes_context_poll_interval` | `HERMES_CONTEXT_POLL_INTERVAL` | `30` | seconds, ssh source |
| `context_token_budget` | `CONTEXT_TOKEN_BUDGET` | `1200` | tokens (~4 chars/token) |
| `result_speech_budget` | `RESULT_SPEECH_BUDGET` | `600` | chars before condensation |
| `task_journal_path` | `TASK_JOURNAL_PATH` | `~/.voice_adapter/tasks.json` | |
| `honcho_api_url` | `HONCHO_API_URL` | `""` (disabled) | optional enrichment only |
| ~~`hermes_sse_url`~~ | ~~`HERMES_SSE_URL`~~ | **removed** | endpoint never existed |
| ~~`hermes_memory_path`~~ | ~~`HERMES_MEMORY_PATH`~~ | **removed** | superseded by context-source URI |
