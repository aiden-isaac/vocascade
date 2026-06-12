# Feature Specification: Hermes Agent Backend Integration

**Feature Branch**: `005-hermes-agent-backend`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "The main brain is a Hermes Agent server (NousResearch
`hermes-agent`, not the Hermes LLM) which contains all tools, memory, and skills.
The fast local adapter must understand the user via context from Hermes without
loading Hermes' tools. Memory must persist, but the project must be deployable on a
fresh machine with zero external services (an existing Honcho server is an optional
upgrade, not a requirement). The agent must hand off naturally: 'what's on my
schedule?' → 'Let me check...' → user can keep talking (and dispatch more tasks)
while the query runs in the background → results are spoken proactively when ready.
Hermes can run on the same machine as the adapter or on a remote host (the
reference deployment runs it remotely on jarlaxle); which one is chosen by config
pointing at where the Hermes files live."

## Context

`voice_adapter/` (feature 004) is a working local-only voice assistant: wakeword →
Pipecat pipeline → Whisper STT → local Qwen LLM → Genie TTS, with barge-in,
dual-signal session termination, and pre-rendered ack audio. It advertises a single
`query_hermes_agent` tool, but the backend behind that tool is not deployed, the
configured SSE endpoint (`/v1/tasks/sse`) does not exist on the real Hermes Agent,
and `PreFetchCache` is a no-op stub. This feature makes the Hermes Agent backend
real.

**Hermes Agent ground truth** (verified 2026-06-11, see [research.md](research.md)):
the API-server gateway adapter exposes an OpenAI-compatible HTTP API on port 8642
with `POST /v1/chat/completions` (SSE streaming, `X-Hermes-Session-Id` continuity,
`X-Hermes-Session-Key` memory scoping), an async runs API (`POST /v1/runs` →
202 + `run_id`, `GET /v1/runs/{run_id}/events` SSE lifecycle stream,
`POST /v1/runs/{run_id}/stop`, `POST /v1/runs/{run_id}/approval`),
`GET /v1/capabilities`, and `GET /health`. Default fresh-install memory is built-in
and file-based — curated `MEMORY.md` + `USER.md` plus SQLite/FTS5 session search
under `~/.hermes/` — requiring zero external services. Honcho is one of eight
optional pluggable memory providers.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Voice Query Through Hermes with Proactive Result (Priority: P1) 🎯 MVP

The user says the wakeword and asks a question the local adapter cannot answer
("what's on my schedule today?"). The local LLM decides to call
`query_hermes_agent`. The dispatch handler returns instantly, so the local
model's own short verbal handoff ("Let me check...") streams with no pause —
by design there is no pre-rendered dispatch filler clip (the wakeword
`acknowledge` clip is the only filler audio in use; a dispatch clip would race
the model's spoken handoff). The adapter
dispatches the query as an asynchronous Hermes **run** (`POST /v1/runs`), receives
a `run_id` immediately, and subscribes to that run's SSE event stream. The
conversation loop stays fully responsive. When the run completes, the adapter
speaks the result proactively — without the user asking again — prefixed by a brief
re-engagement phrase ("About your schedule — ...").

**Why this priority**: This is the end-to-end MVP the user wants to test first:
question → Hermes → proactive spoken answer. Everything else builds on this path.

**Independent Test**: With a Hermes Agent API server running (local or remote) and
the voice stack up: say the wakeword, ask "what's on my schedule today?", verify
(1) the verbal handoff plays within ~1s, (2) `POST /v1/runs` was
issued, (3) the result is spoken unprompted when the run completes.

**Acceptance Scenarios**:

1. **Given** the local LLM emits a `query_hermes_agent` tool call, **When** the
   adapter handles it, **Then** the tool handler returns to the local LLM
   without waiting for the run (so the model's verbal handoff streams
   immediately, with no filler clip), a `HermesTask` is created in state
   `pending`, and `POST /v1/runs` is issued with the prompt.
2. **Given** a run was accepted (202 + `run_id`), **When** the adapter receives the
   acknowledgment, **Then** the task transitions to `executing` and an SSE
   subscription to `/v1/runs/{run_id}/events` is active.
3. **Given** an executing run completes, **When** the completion event arrives and
   neither the user nor the bot is speaking, **Then** the result is spoken within
   2 seconds, prefixed by a re-engagement phrase referencing the original request,
   and the task transitions to `completed`.
4. **Given** the Hermes server is unreachable at dispatch time, **When** the
   `POST /v1/runs` call fails, **Then** the adapter speaks a graceful error ("I
   couldn't reach the main system"), the task is marked `failed`, and the local
   conversation loop continues unaffected.
5. **Given** the runs API is not available on the target server (older build —
   probed via `GET /v1/capabilities` at startup), **When** a dispatch occurs,
   **Then** the adapter falls back to streaming `POST /v1/chat/completions`
   (`stream: true`) in a background task and the same proactive delivery rules
   apply to the buffered result.

---

### User Story 2 — Concurrent Conversation & Multiple In-Flight Tasks (Priority: P1)

While a Hermes run is executing, the user keeps talking. Local small talk is
answered by the local LLM as usual. The user can dispatch a second background task
mid-flight ("hey, could you also summarize my emails?") and the assistant
acknowledges ("Okay, sure") and tracks both runs independently. Results are
delivered in completion order, one at a time, only when the audio channel is idle
(user not speaking, bot not speaking). If a result arrives mid-utterance, it is
buffered and delivered at the next idle moment.

**Why this priority**: Natural handoff is the core UX requirement — the assistant
must never block on a background task, and queued results must not collide with
live speech.

**Independent Test**: Dispatch a slow Hermes task; while it runs, hold a normal
exchange with the assistant and dispatch a second task; verify both `run_id`s are
tracked, conversation latency is unchanged, and both results are spoken in
completion order with no overlapping audio.

**Acceptance Scenarios**:

1. **Given** one task is `executing`, **When** the user makes small talk, **Then**
   the local LLM answers normally with no added latency from the in-flight run.
2. **Given** one task is `executing`, **When** the user requests another background
   task, **Then** a second `HermesTask` with its own `run_id` is created and both
   appear in the transcript context tags.
3. **Given** a result arrives while the user or bot is speaking, **When** the
   delivery queue evaluates it, **Then** delivery is deferred until the channel is
   idle, then spoken.
4. **Given** two results are queued, **When** the channel becomes idle, **Then**
   they are spoken sequentially with a brief pause, each with its own
   re-engagement preamble.
5. **Given** the user barge-ins during a proactive result delivery, **When** the
   interruption is detected, **Then** playback stops, the partially-delivered
   result is marked delivered-interrupted in the transcript (`[interrupted]`
   suffix), and it is not re-queued.

---

### User Story 3 — Fresh-Machine Hermes Deployment (Priority: P1)

A new user clones the repo on a fresh machine and follows the quickstart to stand
up the full stack — including the Hermes Agent backend — with **zero external
services**. Hermes Agent is installed via its official installer, the API-server
gateway adapter is enabled with an `API_SERVER_KEY`, and memory works out of the
box using Hermes' built-in file/SQLite memory (`MEMORY.md`, `USER.md`, FTS5
session search). The reference deployment instead points the adapter at a remote
Hermes on `jarlaxle` over Tailscale; both topologies use the same configuration
surface. An existing Honcho server is a documented opt-in
(`hermes memory setup honcho`), never a requirement.

**Why this priority**: Persistence-without-infrastructure is an explicit project
constraint; without a reproducible bootstrap, no one else can run the project.

**Independent Test**: On a machine (or container) without Honcho/Postgres, follow
`quickstart.md`: install Hermes Agent, enable the API server, start the voice
stack, complete US1's test, restart Hermes, and verify it still remembers facts
told to it before the restart (e.g. "my dog's name is Rex").

**Acceptance Scenarios**:

1. **Given** a fresh machine with no external services, **When** the quickstart is
   followed, **Then** the Hermes API server responds on `/health` and
   `/v1/capabilities`, and US1's independent test passes.
2. **Given** facts shared in a session, **When** the Hermes Agent process is
   restarted, **Then** a later query shows the facts persisted (built-in memory
   files / session DB survive restarts).
3. **Given** the adapter config points at a remote host
   (`HERMES_BASE_URL=http://jarlaxle:8642/v1`), **When** the stack starts, **Then**
   all Hermes traffic goes over Tailscale to the remote API server with bearer
   auth, and no behavior differs from the local topology.
4. **Given** a user with an existing Honcho server, **When** they opt in via
   Hermes' memory provider config, **Then** the voice stack requires no code
   changes — provider choice is entirely Hermes-side configuration.
5. **Given** `API_SERVER_KEY` is unset on the Hermes side, **When** the quickstart
   validation runs, **Then** it warns loudly that the API is unauthenticated
   (test-only mode) and the docs instruct binding to localhost/Tailscale only.

---

### User Story 4 — Adapter Context Hydration from Hermes Memory (Priority: P2)

The local adapter stays fast precisely because it does **not** load Hermes' 40+
tools or skills. Instead, it hydrates a compact context block from Hermes' curated
memory artifacts — `USER.md` (who the user is) and `MEMORY.md` (durable agent
memory) — plus a live summary of in-flight task states. The context source is
chosen by a single config URI pointing at where the Hermes files live:
`file://` for a co-located Hermes (watched via inotify) or `ssh://` for a remote
host like jarlaxle (polled over SFTP). The block is clipped to a token budget and
prepended to the local LLM's system prompt, so the small model "knows" the user
without any per-turn network round-trip.

**Why this priority**: Context quality drives answer quality and correct
dispatch decisions, but US1–US3 function with an empty context block.

**Independent Test**: Point `HERMES_CONTEXT_SOURCE` at a directory containing
`USER.md` ("user's dog is named Rex"); ask the assistant "what's my dog's name?"
and verify it answers locally, with no Hermes round-trip. Modify the file and
verify the cache refreshes (within 2s for `file://`, within one poll interval for
`ssh://`).

**Acceptance Scenarios**:

1. **Given** `HERMES_CONTEXT_SOURCE=file:///home/user/.hermes`, **When** `USER.md`
   or `MEMORY.md` changes, **Then** the cache re-hydrates within 2 seconds via
   inotify and the next turn's system prompt reflects the change.
2. **Given** `HERMES_CONTEXT_SOURCE=ssh://aiden@jarlaxle/home/aiden/.hermes`,
   **When** the poll interval elapses, **Then** changed files are fetched over
   SFTP (mtime/size guarded) and merged into the cache; an unreachable host
   retains the last snapshot and logs a warning.
3. **Given** the context files exceed the token budget, **When** the block is
   built, **Then** `USER.md` is prioritized over `MEMORY.md`, content is truncated
   at section boundaries, and the final block stays within
   `CONTEXT_TOKEN_BUDGET`.
4. **Given** a fresh Hermes install where `MEMORY.md`/`USER.md` do not exist yet,
   **When** the adapter starts, **Then** it proceeds with an empty context block
   (no crash, single startup warning).
5. **Given** the cache is cold at startup, **When** the first wakeword arrives,
   **Then** the session waits for warm-up up to 10 seconds, then proceeds with
   whatever context is available.
6. **Given** the context block is present, **When** the local LLM is prompted,
   **Then** the only tools advertised remain the adapter's own dispatch/cancel
   tools — Hermes tool and skill schemas are never loaded into the local model.

---

### User Story 5 — Task Status, Cancellation & Voice Approvals (Priority: P2)

The user can ask "is that done yet?" and the local LLM answers from transcript
task tags without a Hermes round-trip. The user can say "cancel that" — if the run
is still cancellable, the adapter calls `POST /v1/runs/{run_id}/stop`; the
existing `can_cancel()` guard refuses to cancel a run in a non-cancellable phase
and tells the user it will report when done. If a run emits an approval-required
event, the adapter speaks the approval request and relays the user's spoken
yes/no via `POST /v1/runs/{run_id}/approval`.

**Why this priority**: Lifecycle control makes background tasks trustworthy, but
the happy path (US1/US2) works without it.

**Independent Test**: Dispatch a slow run; ask "is that done yet?" (answered from
tags, verify no new Hermes request); say "cancel that" and verify the stop call
and a confirmation utterance; simulate an approval event and verify the spoken
prompt + relayed decision.

**Acceptance Scenarios**:

1. **Given** an executing task tagged in the transcript window, **When** the user
   asks about its status, **Then** the local LLM answers from the
   `[TASK:<id> STATE:<state>]` tags without contacting Hermes.
2. **Given** a cancellable executing run, **When** the user requests cancellation,
   **Then** the adapter calls `/v1/runs/{run_id}/stop`, marks the task
   `cancelled`, confirms verbally, and any late result for that run is discarded.
3. **Given** `can_cancel()` returns False, **When** the user requests
   cancellation, **Then** the adapter explains the task can't be cancelled and
   will be reported when done.
4. **Given** a run emits an approval-required event, **When** the adapter receives
   it, **Then** it speaks the approval request at the next idle moment and relays
   the user's spoken decision to `/v1/runs/{run_id}/approval`; if the user doesn't
   respond before the session ends, the approval remains pending and is
   re-announced on the next session.

---

### User Story 6 — Result Persistence Across Sessions & Adapter Restarts (Priority: P3)

A conversation session may end (farewell → passive listening) while runs are still
executing. Tracked tasks survive session teardown in the adapter process; on the
next wakeword session, pending results are announced after the greeting ("While
you were away, the email summary finished — want to hear it?"). If the adapter
process itself restarts, the task registry is restored from a small disk journal
and event subscriptions are re-established for runs still executing (or their
final state fetched via `GET /v1/runs/{run_id}`).

**Why this priority**: Long-running Jarvis-style tasks routinely outlive a voice
session. Valuable but not needed to validate the core loop.

**Acceptance Scenarios**:

1. **Given** a session ends with a run executing, **When** the run completes while
   passive, **Then** the result is queued, not spoken into a dead session.
2. **Given** queued undelivered results exist, **When** the next session starts,
   **Then** they are announced after the wakeword acknowledgment.
3. **Given** the adapter restarts with runs in flight, **When** it boots, **Then**
   it reloads the task journal, re-subscribes to event streams for `executing`
   runs, and reconciles terminal states via `GET /v1/runs/{run_id}`.

---

### Edge Cases

- **SSE event stream drops mid-run**: reconnect with exponential backoff (1s →
  60s cap); on reconnect, reconcile state via `GET /v1/runs/{run_id}` so a
  completion that happened during the gap is not lost.
- **Duplicate or out-of-order events**: task state machine is idempotent; an event
  for an already-terminal task is ignored (except logging).
- **Result for a cancelled task arrives**: discarded, never spoken (existing
  US3/004 rule, now keyed by `run_id`).
- **Run fails or times out server-side**: failure event → task `failed`; spoken
  once at the next idle moment ("I couldn't finish the schedule check").
- **Hermes capabilities probe fails at startup**: adapter starts anyway in
  degraded dispatch mode (chat-completions fallback) and logs the reason; probe
  retried lazily on next dispatch.
- **Context files temporarily unreadable / SSH host down**: serve stale snapshot;
  never block a turn on hydration (only the initial warm gate may wait, ≤10s).
- **Clock skew between adapter and remote Hermes**: task timestamps for display
  are local; remote mtimes are used only for change detection, never compared to
  local clocks.
- **Very large result text**: results are summarized for speech beyond a
  configurable length (default ~600 chars) by the local LLM, with the full text
  retained in the transcript turn.
- **`API_SERVER_KEY` rotation**: 401 responses mark the client degraded with a
  clear log + spoken error distinct from "unreachable".

## Requirements *(mandatory)*

### Functional Requirements

**Dispatch & task lifecycle**

- **FR-001**: The adapter MUST dispatch `query_hermes_agent` tool calls as
  asynchronous Hermes runs via `POST /v1/runs`, returning control to the local
  LLM immediately (non-blocking handoff).
- **FR-002**: The adapter MUST probe `GET /v1/capabilities` at startup and fall
  back to background `POST /v1/chat/completions` (`stream: true`) dispatch when
  the runs API is unavailable, preserving identical delivery semantics.
- **FR-003**: Every dispatch MUST create a `HermesTask` keyed by the
  server-issued `run_id` (the local `task_YYYYMMDD_HHMMSS_XX` id is retained as a
  human-readable alias), with lifecycle
  `pending → executing → completed | failed | cancelled`.
- **FR-004**: The adapter MUST subscribe to `GET /v1/runs/{run_id}/events` (SSE)
  per dispatched run and reconnect with exponential backoff (initial 1s, cap
  60s), reconciling missed events via `GET /v1/runs/{run_id}`.
- **FR-005**: The dispatch handler MUST return to the local LLM without
  awaiting any Hermes network round-trip so the model's spoken handoff streams
  immediately. No dispatch filler clip is played — the wakeword `acknowledge`
  clip is the only pre-rendered audio in use (a dispatch clip would collide
  with the model's own speech).
- **FR-006**: On user cancellation of a cancellable run, the adapter MUST call
  `POST /v1/runs/{run_id}/stop` and mark the task `cancelled`; the existing
  `can_cancel()` guard MUST be honored for non-cancellable phases.
- **FR-007**: Approval-required run events MUST be spoken to the user and the
  decision relayed via `POST /v1/runs/{run_id}/approval`.

**Proactive delivery**

- **FR-008**: Completed-run results MUST enter a FIFO delivery queue and be
  spoken only when the audio channel is idle (no user speech, no bot speech);
  results arriving mid-utterance are buffered.
- **FR-009**: Each proactive delivery MUST begin with a short re-engagement
  preamble referencing the originating request.
- **FR-010**: Results for `cancelled` tasks MUST be discarded; results for tasks
  whose session has ended MUST be retained and announced at the next session
  start (US6).
- **FR-011**: Barge-in during proactive delivery MUST stop playback immediately;
  the interrupted result is committed to the transcript with the interruption
  marker and not re-queued.
- **FR-012**: Results longer than a configurable speech budget MUST be condensed
  for speech by the local LLM, retaining the full text in the transcript turn.

**Session & auth**

- **FR-013**: All Hermes API calls MUST send `Authorization: Bearer
  $HERMES_API_KEY` and a stable `X-Hermes-Session-Key` (config, default
  `voice-satellite`) so Hermes scopes long-term memory to this user across
  sessions; conversational continuity uses `X-Hermes-Session-Id` per
  voice session.
- **FR-014**: The deprecated `HERMES_SSE_URL` config (pointing at the
  non-existent `/v1/tasks/sse`) MUST be removed; event URLs are derived from
  `HERMES_BASE_URL` and the `run_id`.

**Context hydration**

- **FR-015**: The adapter MUST hydrate a context block from Hermes' built-in
  memory artifacts `USER.md` and `MEMORY.md`, plus a live in-flight task summary,
  and prepend it to the local LLM system prompt each turn from an in-memory cache
  (read < 1 ms).
- **FR-016**: The context source MUST be selected by a single config URI
  `HERMES_CONTEXT_SOURCE`: `file://<path>` (watchdog/inotify, 2s refresh),
  `ssh://<user>@<host>/<path>` (SFTP polling at `HERMES_CONTEXT_POLL_INTERVAL`,
  default 30s, mtime/size change detection), or `none` (feature disabled).
- **FR-017**: The context block MUST be clipped to `CONTEXT_TOKEN_BUDGET`
  (default 1200 tokens, ~4 chars/token heuristic), prioritizing `USER.md`, then
  task states, then `MEMORY.md`, truncating at markdown section boundaries.
- **FR-018**: The local LLM MUST NOT be given Hermes tool or skill schemas — its
  toolset is limited to the adapter's own dispatch/cancel/status tools.
- **FR-019**: The cache-warm gate MUST hold the first utterance up to 10 seconds
  and then proceed with available context; hydration failures MUST serve the last
  snapshot, never block a turn.
- **FR-020**: An optional Honcho source (`HONCHO_API_URL`, empty = disabled) MAY
  be merged into the snapshot for users running Honcho; its absence MUST NOT
  degrade any other behavior.

**Deployment & persistence**

- **FR-021**: The repo MUST document and script a fresh-machine Hermes Agent
  bootstrap (official installer, API-server adapter enabled with
  `API_SERVER_KEY`, default built-in memory) requiring zero external services.
- **FR-022**: Both topologies — Hermes co-located with the adapter and Hermes on
  a remote Tailscale host (reference: jarlaxle) — MUST be supported purely
  through configuration (`HERMES_BASE_URL` + `HERMES_CONTEXT_SOURCE`).
- **FR-023**: Honcho MUST remain a Hermes-side opt-in memory provider; the voice
  stack MUST NOT require it nor change behavior based on the provider choice
  (beyond the optional FR-020 source).
- **FR-024**: The task registry MUST be journaled to disk
  (`~/.voice_adapter/tasks.json` by default) so adapter restarts re-subscribe to
  executing runs and reconcile terminal states (US6).
- **FR-025**: All new settings MUST be added to `AdapterConfig`, loaded from
  `.env`, and reflected in `.env.example`.

### Key Entities

- **HermesRunClient**: HTTP/SSE client for the API-server adapter: dispatch run,
  stream run events, stop, approve, fetch run state, probe capabilities, plus the
  chat-completions fallback path. Wraps auth + session headers.
- **HermesTask** (extended): existing entity gains `run_id`, `failed` state,
  originating request text, result text, `delivered` flag, and session linkage.
- **RunEvent**: parsed SSE lifecycle event (`accepted`, `progress`,
  `approval_required`, `completed`, `failed`, …) with `run_id` and payload.
- **ProactiveResult**: queued deliverable — task reference, speech text
  (possibly condensed), preamble, enqueue time, delivery state.
- **DeliveryCoordinator**: owns the result queue and channel-idle detection;
  the only component allowed to inject system-initiated speech.
- **ContextSource**: abstraction with `LocalFileSource` (watchdog) and
  `SshFileSource` (SFTP poll) implementations, selected from the
  `HERMES_CONTEXT_SOURCE` URI; optional `HonchoSource`.
- **ContextSnapshot** (extended): `user_profile` (USER.md), `agent_memory`
  (MEMORY.md), `pending_tasks`, `last_updated`, `source_health`.
- **TaskJournal**: small JSON disk journal of non-terminal tasks + undelivered
  results for restart recovery.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From the local LLM's tool-call decision, the dispatch handler
  returns within 100 ms and the verbal handoff ("Let me check…") streams with
  no perceptible pause — no Hermes network round-trip on the speech path.
- **SC-002**: A completed run's result is spoken within 2 s of the completion
  event when the channel is idle.
- **SC-003**: Local conversational latency (speech end → first response audio) is
  unchanged (< 3 s, per 004 SC-001) while ≥ 2 runs are executing.
- **SC-004**: The adapter correctly tracks ≥ 3 concurrent runs with zero
  cross-task result mix-ups in a 20-dispatch soak test.
- **SC-005**: Fresh-machine bootstrap (US3) completes in ≤ 30 minutes with zero
  external services, and memory survives a Hermes restart.
- **SC-006**: Context block construction adds < 50 ms to prompt assembly; cache
  reads are < 1 ms; a `file://` source reflects changes within 2 s and an
  `ssh://` source within one poll interval.
- **SC-007**: With the context block populated, the assistant answers
  profile-derived questions ("what's my dog's name?") locally with no Hermes
  dispatch in ≥ 9/10 trials.
- **SC-008**: After an adapter restart with one run in flight, the result is
  still delivered (immediately if completed during downtime, else on completion).
- **SC-009**: 24-hour soak: no orphaned SSE connections, no unbounded growth of
  the task registry or delivery queue.

## Assumptions

- Hermes Agent (NousResearch `hermes-agent`) is the backend; its API-server
  gateway adapter is the only integration surface this feature consumes. No
  Hermes core code is modified.
- The reference deployment runs Hermes on `jarlaxle` reachable over Tailscale;
  the adapter machine has SSH key access to jarlaxle for the `ssh://` context
  source.
- Hermes' built-in memory (`MEMORY.md`, `USER.md`, SQLite session DB under
  `~/.hermes/`) is the fresh-machine default; Honcho/Mem0/etc. are Hermes-side
  provider choices invisible to this feature (except optional FR-020).
- The runs API surface (`/v1/runs*`) exists on current `hermes-agent` main; exact
  event names/payloads are verified against the live server during Phase 0
  (contract test) since upstream may evolve — `contracts/hermes-api.md` records
  the consumed subset.
- The local LLM endpoint supports OpenAI tool-calling (already required by 004).
- Single concurrent voice session (existing 004 constraint) — but multiple
  concurrent background runs.
- `voice_satellite/gateway/hermes_client.py` remains for the chat-completions
  path and is extended (or wrapped) rather than replaced, fixing its endpoint to
  `{base}/chat/completions` with `/v1` carried in `HERMES_BASE_URL`.

## Out of Scope

- Proactively waking the satellite speaker from passive mode to announce a result
  with no active session (deferred; results queue for the next session instead).
- Bidirectional Hermes → adapter push for anything other than subscribed run
  events (no webhook adapter usage in this feature).
- Multi-user / multi-satellite support.
- The 004 offline handler (US7) and morning briefing — unchanged by this feature.
