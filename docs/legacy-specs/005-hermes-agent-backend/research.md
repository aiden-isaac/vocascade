# Research: Hermes Agent Backend Integration

**Branch**: `005-hermes-agent-backend` | **Date**: 2026-06-11 | **Spec**: [spec.md](spec.md)

This document records what was verified about NousResearch's `hermes-agent`
(2026-06-11) and the design decisions derived from it. Where upstream may evolve,
the consumed subset is pinned in [contracts/hermes-api.md](contracts/hermes-api.md)
and re-verified by a Phase-0 contract test against the live server.

## 1. What Hermes Agent Is

**Source**: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent),
[AGENTS.md](https://github.com/NousResearch/hermes-agent/blob/main/AGENTS.md),
[hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com/)

- Open-source (MIT) autonomous agent by Nous Research. Model-agnostic (works with
  local endpoints, OpenRouter, Anthropic, OpenAI, …) — it is an **agent harness**,
  not a model; "Hermes LLM" is unrelated for our purposes.
- Installed via official platform installers (bash one-liner); manages its own
  deps (uv, Python 3.11, Node, ripgrep, ffmpeg). CLI: `hermes`; messaging/API
  ingress: `hermes gateway`.
- User state lives under `~/.hermes/` (profile-aware via `get_hermes_home()`):
  `config.yaml` (behavioral settings), `.env` (secrets), sessions SQLite
  (`state.db`, FTS5 full-text search), `skills/`, logs, gateway state.
- **Tools**: 40+ built-in (`tools/registry.py` auto-discovery): terminal,
  read/write/patch files, web_search, browser, vision, delegate_task, memory,
  todo, cronjob, send_message, etc. Toolsets are bundled per platform context.
- **Skills**: directories with `SKILL.md` + scripts/references, under
  `~/.hermes/skills/`; injected as user messages; auto-curated/archived.
- **Gateway**: ~20 platform adapters (Telegram, Discord, Slack, WhatsApp,
  HomeAssistant, …, Webhook, **API server**) all inheriting `BaseAdapter`,
  sharing session management.

**Implication for us**: the adapter integrates exclusively through the API-server
gateway adapter. We never import Hermes code, never load its toolsets into the
local LLM, and treat `~/.hermes/` artifacts as read-only context inputs.

## 2. API Server Adapter (the integration surface)

**Source**: [gateway/platforms/api_server.py](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/api_server.py),
[.plans/openai-api-server.md](https://github.com/NousResearch/hermes-agent/blob/main/.plans/openai-api-server.md),
[Open WebUI integration doc](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/messaging/open-webui.md)

Verified surface (defaults `API_SERVER_HOST=127.0.0.1`, `API_SERVER_PORT=8642`,
bearer auth via `API_SERVER_KEY` — **auth is skipped entirely if the key is
unset**, test-only mode):

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat/completions` | OpenAI-compatible chat; `stream: true` → SSE; opt-in continuity via `X-Hermes-Session-Id`; long-term memory scoping via `X-Hermes-Session-Key` (≤256 chars, requires API key) |
| `POST /v1/runs` | **Async execution** — returns `run_id` immediately (HTTP 202) |
| `GET /v1/runs/{run_id}` | Run status/result retrieval |
| `GET /v1/runs/{run_id}/events` | SSE stream of structured lifecycle events (30s keepalives) |
| `POST /v1/runs/{run_id}/stop` | Interrupt execution |
| `POST /v1/runs/{run_id}/approval` | Resolve pending human-in-the-loop approvals |
| `GET /v1/models`, `GET /v1/capabilities` | Discovery; capabilities is machine-readable feature detection |
| `GET /health`, `GET /health/detailed` | Health checks |
| `/api/sessions*` | Session CRUD, history, fork, per-session chat/stream |

Other verified details: 10 MB max body, 65,536 chars per content part,
idempotency cache (300s TTL), responses stored in SQLite (`response_store.db`),
sessions shared with CLI via `SessionDB`/`state.db`, CORS/CSP headers, requests
from unconfigured browser origins rejected.

### Decision D1: Use `/v1/runs` as the dispatch primitive

**Rationale**: it returns a `run_id` in one round-trip (202) so the voice loop is
never blocked; per-run SSE event streams give us lifecycle (accepted → … →
completed/failed) instead of inferring completion from a token stream ending;
`/stop` gives real cancellation (004's `can_cancel()` guard finally has teeth);
`/approval` enables voice-relayed approvals. Maps 1:1 onto the existing
`HermesTask` state machine.

**Alternatives considered**:
- `POST /v1/chat/completions` with `stream: true` per dispatch (current
  behavior): no task identity, no cancellation, completion only inferable from
  stream close, errors mid-stream ambiguous. Kept only as the **fallback** when
  the capabilities probe says runs are unavailable (older Hermes builds).
- Hermes **webhook adapter** (Hermes pushes to us): inverts the connection
  direction, requires the adapter to expose an ingress and Hermes-side config;
  more moving parts for the same information the run event stream already carries.
  Rejected for this feature.
- `/api/sessions/{id}/chat/stream`: session-centric, still synchronous-stream
  shaped; doesn't add task lifecycle.

### Decision D2: Replace `HERMES_SSE_URL` (`/v1/tasks/sse`) — it does not exist

The 004 design assumed a single global task-completion SSE feed at
`/v1/tasks/sse`. The real API has **per-run** event streams. `PipecatBridge`
becomes a per-run subscriber pool rather than one persistent global listener.
Reconnection/backoff logic from 004's design carries over per-stream, plus state
reconciliation via `GET /v1/runs/{run_id}` after a gap.

### Decision D3: Session identity headers

- `X-Hermes-Session-Key: voice-satellite` (configurable) — **stable across
  everything**; scopes Hermes' long-term memory to this user. Sent on every call.
- `X-Hermes-Session-Id: <uuid per voice session>` — conversational continuity
  within one wakeword→farewell session; rotated on session teardown. (Upstream
  derives one deterministically if absent; we send our own for explicit control.)

## 3. Memory & Persistence

**Source**: [Memory Providers doc](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers/),
[Honcho doc](https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho),
[honcho.dev integration guide](https://honcho.dev/docs/v3/guides/integrations/hermes)

- **Fresh-install default (zero external services)**: curated `MEMORY.md` +
  `USER.md` files plus SQLite session search (FTS5) — run automatically, no
  configuration. This is exactly the "persists on a fresh machine" requirement.
- **8 optional provider plugins**: Honcho, OpenViking, Mem0, Hindsight,
  Holographic, RetainDB, ByteRover, Supermemory — all implementing the
  `MemoryProvider` ABC (`agent/memory_provider.py`), orchestrated by
  `agent/memory_manager.py` (`sync_turn`, `prefetch`, `shutdown`).
- Honcho opt-in: `hermes memory setup honcho`; active when
  `memory.provider: honcho` in `config.yaml`; its own config resolves
  `$HERMES_HOME/honcho.json` → `~/.hermes/honcho.json` → `~/.honcho/config.json`.

### Decision D4: Built-in memory is the default; Honcho is Hermes-side opt-in

**Rationale**: satisfies the fresh-machine constraint with literally zero setup;
the provider choice is entirely Hermes configuration, invisible to the voice
stack. The owner's existing Honcho server is enabled in *Hermes'* config, not
ours. The adapter keeps an **optional** direct Honcho read
(`HONCHO_API_URL`, empty = disabled) only as a context-hydration enrichment
(FR-020), never a dependency.

**Alternative rejected**: requiring Honcho (docker-compose Postgres+pgvector on
every install) — directly contradicts the fresh-machine goal.

## 4. Context Hydration for the Fast Adapter

The local Qwen adapter must "understand the user" without loading Hermes' tools.
Hermes happens to maintain exactly the right artifacts for this: `USER.md` (who
the user is) and `MEMORY.md` (curated durable memory) — small, human-readable,
LLM-curated markdown.

### Decision D5: Hydrate from `USER.md` + `MEMORY.md`, source selected by URI

`HERMES_CONTEXT_SOURCE` is a single config URI saying **where the Hermes files
live** (per the owner's direction):

- `file:///home/<user>/.hermes` — co-located Hermes. `watchdog`/inotify observer
  (already a project dependency from 004), 2s refresh on change, polling
  fallback if inotify limits hit.
- `ssh://aiden@jarlaxle/home/aiden/.hermes` — remote Hermes (the reference
  deployment). SFTP polling via **asyncssh** every `HERMES_CONTEXT_POLL_INTERVAL`
  (default 30s); fetch only when mtime/size changed; key-based auth using the
  user's existing SSH setup over Tailscale.
- `none` — hydration disabled (empty context block).

**Alternatives considered for remote**:
- **SSHFS mount + file:// watcher**: makes the remote case look local, but inotify
  does not fire over SSHFS/NFS, so we'd silently degrade to polling anyway —
  with an extra system-level mount to manage. Rejected.
- **Hermes HTTP API**: there is no "give me your MEMORY.md" endpoint; abusing
  `/v1/chat/completions` to ask the agent to print its memory burns an agent
  turn per refresh and is slow/nondeterministic. Rejected.
- **rsync/scp cron**: equivalent to SFTP polling but outside the process,
  harder to gate `is_warm` on. Rejected.
- **Syncthing/NFS share**: extra infrastructure on a fresh machine. Rejected.

**Note on profiles**: Hermes is profile-aware (`get_hermes_home()`); the URI
path points at the *active profile's* home, documented in quickstart.

### Decision D6: Token budget + priority order

`USER.md` (highest value per token) → in-flight task summary (live, generated) →
`MEMORY.md` (truncated at markdown section boundaries). Default budget 1200
tokens (~4 chars/token heuristic — no tokenizer dependency). Budget keeps the
small local model's prompt fast (its prefill is latency-critical).

## 5. Dispatch UX & Delivery

### Decision D7: DeliveryCoordinator owns all system-initiated speech

004's `inject_text` lets any background task push frames at any moment — fine for
one task, race-prone for several. A single `DeliveryCoordinator` with a FIFO
queue and channel-idle detection (no `UserStartedSpeaking` since last
`BotStoppedSpeaking`, no TTS in flight) becomes the only path for proactive
speech: run results, failure notices, approval requests, next-session backlog
announcements. Re-engagement preambles are generated from the originating
request text ("About your schedule — …").

### Decision D8: Task journal for restart recovery

Non-terminal tasks + undelivered results are journaled to
`~/.voice_adapter/tasks.json` (atomic write-replace, same pattern as 004's
offline queue). On boot: reload, re-subscribe `executing` runs to their event
streams, reconcile already-terminal runs via `GET /v1/runs/{run_id}`. Cheap
insurance; no database.

## 6. Library Choices

| Need | Choice | Rationale |
|---|---|---|
| HTTP + SSE client | `httpx` (existing dep) | Already used by `HermesClient`; `client.stream()` handles SSE lines; no new dep for the runs API |
| Remote file fetch | `asyncssh` (**new dep**) | Pure-asyncio SFTP, key auth, well-maintained; alternative `paramiko` is sync (would need thread executor) |
| Local file watch | `watchdog` (existing dep) | Already specified in 004 for `PreFetchCache` |
| Journal | stdlib `json` + `os.replace` | Atomic enough for a single-writer journal |

## 7. Open Questions — RESOLVED in Phase 0 (T101/T102, 2026-06-12)

All three pinned against the live jarlaxle server; details in
`contracts/hermes-api.md` §"Resolved open questions".

- **OQ-1** ✅ Event vocabulary confirmed: `run.started`, `message.delta`,
  `reasoning.available`, `tool.started/completed/failed`, `approval.request`,
  `approval.responded`, `run.completed`, `run.failed`, `run.cancelled`. Note
  the single-subscriber queue semantics: reconnect after disconnect ⇒ 404 ⇒
  reconcile via `GET /v1/runs/{id}`.
- **OQ-2** ✅ Yes — `/v1/runs` parses `X-Hermes-Session-Key` via the shared
  header parser (echoed back; requires API-key auth).
- **OQ-3** ✅ No server-side result cap below the 10 MB body limit ⇒ FR-012
  speech condensation is mandatory.

## Sources

- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
- [hermes-agent AGENTS.md](https://github.com/NousResearch/hermes-agent/blob/main/AGENTS.md)
- [gateway/platforms/api_server.py](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/api_server.py)
- [.plans/openai-api-server.md](https://github.com/NousResearch/hermes-agent/blob/main/.plans/openai-api-server.md)
- [Memory Providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers/) · [Honcho Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho)
- [Honcho ↔ Hermes integration](https://honcho.dev/docs/v3/guides/integrations/hermes)
- [Open WebUI ↔ Hermes guide](https://docs.openwebui.com/getting-started/quick-start/connect-an-agent/hermes-agent/)
