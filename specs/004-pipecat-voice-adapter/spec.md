# Feature Specification: Pipecat Voice Adapter

**Feature Branch**: `pipecat`

**Created**: 2026-06-02

**Status**: Draft

## User Scenarios & Testing *(mandatory)*

### User Story 0 — Always-On Satellite Client (`satellite.py`) (Priority: P0)

A lightweight daemon (`satellite.py`) runs locally on the edge node (e.g., Athrogate) and serves as the always-listening physical interface. It continuously monitors the local microphone for the custom wake word ("Renna") using openWakeWord with a local `.tflite` model. Upon wake word detection, it instantly opens a WebSocket connection over the Tailscale network to the central FastAPI `/ws` endpoint on Jarlaxle and begins streaming raw PCM audio. On session timeout (silence detection, an explicit end phrase, or server-side close), it tears down the connection and returns to its passive offline listening loop. 

**Why this priority**: It replaces the browser-based client and is the prerequisite for hands-free interaction without manual intervention.

**Independent Test**: Run `satellite.py`, speak "Renna", and verify a WebSocket connection is made to a mock server. Stop speaking and verify the connection closes.

**Acceptance Scenarios**:

1. **Given** the client is running, **When** the wake word "Renna" is spoken, **Then** it opens a WebSocket to `/ws` and streams audio.
2. **Given** an active session, **When** silence is detected or the server closes the connection, **Then** the client tears down the connection and resumes wake word listening.
3. **Given** the client is streaming PCM audio from the server, **When** audio is received, **Then** it plays back immediately on local speakers.

---

### User Story 1 — Voice Pipeline Orchestration via Pipecat (Priority: P1)

The user places a satellite device (e.g., Athrogate) in a room. The
central server runs a Pipecat pipeline with `FastAPIWebsocketTransport` that handles
all real-time audio I/O. When the satellite client triggers on the wake word and connects
via WebSocket, Pipecat's STT processor (faster-whisper)
and speaks after wake word activation, Pipecat's STT processor (faster-whisper)
transcribes the audio. The central adapter orchestrator receives the transcript,
performs a pre-flight check (online/offline), pulls hot context from the
pre-fetch cache, constructs a prompt, and routes to Qwen via the Hermes
gateway. If the response is a direct answer, tokens stream through Pipecat's
TTS processor (Genie) back to the user as synthesized audio. If Hermes
dispatch is required, the adapter immediately streams a brief acknowledgment
phrase ("Let me check on that") to the TTS queue before dispatching, filling
the silence while Hermes works. The pipeline returns to listening mode after
dispatch.

**Why this priority**: This is the core interaction loop. Without the Pipecat
pipeline and adapter orchestrator, no voice interaction is possible. Every
other feature depends on this path being functional.

**Independent Test**: Start the adapter with Hermes gateway and Genie TTS
running. Connect the satellite WebSocket client, speak a simple question, and
verify the full round-trip: audio in → STT transcript → Hermes response →
TTS audio out. No offline handler, pre-fetch cache, or SSE bridge is required
for this test — only the pipeline, adapter, and backends.

**Acceptance Scenarios**:

1. **Given** the adapter is running with Pipecat pipeline active, **When** the
   user sends audio via WebSocket, **Then** the audio is transcribed by
   faster-whisper and the transcript is delivered to the adapter within 500 ms
   of speech end.
2. **Given** the adapter receives a transcript, **When** the Hermes gateway is
   reachable, **Then** the adapter constructs a prompt with available context,
   sends to Qwen, and streams response tokens to the Genie TTS processor.
3. **Given** a response requires Hermes task dispatch, **When** the adapter
   detects the routing decision, **Then** a brief acknowledgment phrase is
   immediately streamed to TTS before the Hermes dispatch occurs, and a
   `hermes_task_id` (format: `task_YYYYMMDD_HHMMSS_XX`) is generated.
4. **Given** the Hermes gateway is unreachable, **When** the adapter attempts
   to send a transcript, **Then** the adapter notifies the user via TTS
   ("I can't reach the backend right now") and enters degraded mode with
   automatic retry using exponential backoff capped at 60 seconds.
5. **Given** the Pipecat pipeline is running, **When** no client is connected,
   **Then** the system consumes minimal resources and accepts new connections
   immediately.

---

### User Story 2 — Genie TTS Integration (Priority: P1)

The system uses a custom Pipecat `TTSService` subclass (`GenieTTSService`)
that sends synthesis requests to the Genie TTS HTTP server (GPT-SoVITS V2Pro
architecture) instead of any commercial TTS provider. The
service receives text frames from the pipeline, calls the Genie `/tts`
endpoint, and yields `AudioRawFrame` objects back into the pipeline. The
service handles input sanitization (skip non-alphanumeric, ensure trailing
punctuation, lowercase conversion), streaming chunked PCM responses, and
graceful degradation when the Genie server is unreachable.

**Why this priority**: Without TTS, the system cannot produce audible responses.
Genie TTS is a hard requirement — no cloud TTS fallback is acceptable.

**Independent Test**: Initialize the GenieTTSService with valid Genie server
configuration. Send a text string through the service and verify that PCM
audio frames are yielded at the correct sample rate (32 kHz). Test with Genie
server offline to verify degraded mode behavior.

**Acceptance Scenarios**:

1. **Given** valid Genie TTS configuration (server URL, character name, ONNX
   model path, reference audio, reference text), **When** the adapter starts,
   **Then** the GenieTTSService registers the character with the Genie server
   and reports ready status.
2. **Given** a text frame arrives in the pipeline, **When** the GenieTTSService
   processes it, **Then** PCM audio chunks are streamed from the Genie `/tts`
   endpoint and yielded as `AudioRawFrame` objects at 32 kHz sample rate.
3. **Given** the Genie server is unreachable, **When** the GenieTTSService
   attempts synthesis, **Then** it enters degraded mode, logs the error, and
   does not crash the pipeline.
4. **Given** the input text contains only punctuation or non-alphanumeric
   characters, **When** the GenieTTSService receives it, **Then** the input is
   silently skipped without sending to the Genie server.
5. **Given** a synthesis is in progress, **When** a barge-in or cancellation
   signal arrives, **Then** the GenieTTSService cancels the in-flight HTTP
   request and releases resources cleanly.

---

### User Story 3 — Hermes Task Dispatch & Async Result Injection (Priority: P1)

When Hermes completes a long-running background task (browser automation via
athrogate's MCP server, multi-step tool chain) after the voice pipeline has
returned to idle listening mode, the result must be spoken to the user
unprompted. The `pipecat_bridge` component maintains a persistent SSE listener
against the Hermes gateway. When a task completion event arrives matching a
tracked `hermes_task_id` currently in `executing` state, the bridge injects
the response text directly into Pipecat's TTS queue as a system-initiated
turn — the user hears the result even if the microphone is idle.

**Why this priority**: The Hermes agent framework's value is in long-running
autonomous tasks. Without async result injection, the system is limited to
synchronous request-response, which defeats the purpose of the agent layer.

**Independent Test**: Dispatch a Hermes task via the adapter, verify the
`hermes_task_id` is tracked as `executing` in the transcript manager. Simulate
an SSE completion event from the Hermes gateway and verify that the result
text is injected into the TTS queue and spoken to the user without any user
input trigger.

**Acceptance Scenarios**:

1. **Given** the bridge is listening for SSE events, **When** a task completion
   event arrives with a matching `hermes_task_id`, **Then** the response text
   is injected into Pipecat's TTS queue and the transcript entry is updated
   to `completed`.
2. **Given** a task completion arrives during an active user turn, **When** the
   user is currently speaking or listening to a response, **Then** the
   injection is buffered until the current turn's TTS finishes, then spoken.
3. **Given** the SSE connection to Hermes drops, **When** the bridge detects
   the disconnection, **Then** it reconnects with exponential backoff and
   resumes listening without losing tracked task state.
4. **Given** a `hermes_task_id` was cancelled before completion, **When** a
   completion event arrives for that task, **Then** the result is discarded
   and not spoken.

---

### User Story 4 — Barge-In & Interruption Handling (Priority: P1)

At any point during assistant audio playback, the user can begin speaking
(barge-in). The system immediately stops current audio playback, discards the
pending Hermes response for the current task, marks the task as cancelled in
the transcript, and accepts new input cleanly. In-flight tool calls on
athrogate are not killed — they complete and results are discarded.
Conversation continuity is preserved by Honcho's long-term memory.

**Why this priority**: Barge-in is essential for natural conversation. Without
it, the user must wait for potentially long responses to finish before
speaking again.

**Independent Test**: Start a conversation that produces a long response.
While the assistant is speaking, begin speaking. Verify that audio playback
stops within 200 ms, the new input is accepted, and the next response
demonstrates awareness of the interruption context.

**Acceptance Scenarios**:

1. **Given** the assistant is speaking (TTS audio playing), **When** the user
   begins speaking (detected by Pipecat's VAD), **Then** the TTS output is
   immediately stopped and the pipeline accepts new audio input.
2. **Given** a barge-in occurs during a Hermes task, **When** the task has a
   pending response, **Then** the response is discarded and the task is marked
   `cancelled` in the transcript manager.
3. **Given** a barge-in occurs, **When** in-flight tool calls are running on
   athrogate, **Then** the tool calls are allowed to complete — results are
   discarded silently upon return.
4. **Given** the user interrupts, **When** the new utterance is processed,
   **Then** conversation continuity is maintained via Honcho's session history.

---

### User Story 5 — Transcript Manager & Execution Graph (Priority: P2)

The transcript manager transforms conversation history from a static text log
into a tagged state machine. It maintains a hard-pruned 5–7 turn sliding
window of recent turns. Every turn involving a Hermes dispatch is tagged with
its `hermes_task_id` and a `hermes_state` field (`pending`, `executing`, or
`completed`). When Qwen receives a cancellation or override request, it checks
the transcript for any task currently tagged `executing` and logically blocks
the cancellation, informing the user that the task is already in-flight.

**Why this priority**: The transcript manager enables intelligent routing
decisions and prevents race conditions in task dispatch. It depends on the
core pipeline (US1) being functional first.

**Independent Test**: Instantiate the transcript manager, append several turns
with mixed states (pending, executing, completed). Verify the sliding window
prunes correctly, state updates propagate, and the execution-guard logic
blocks cancellation of in-flight tasks.

**Acceptance Scenarios**:

1. **Given** the transcript manager has 8 turns appended, **When** querying
   the current window, **Then** only the most recent 5–7 turns are returned.
2. **Given** a turn is tagged with `hermes_task_id` and state `pending`,
   **When** Hermes acknowledges receipt, **Then** the state updates to
   `executing`.
3. **Given** a task is tagged `executing`, **When** Qwen receives a user
   cancellation request for that task, **Then** the cancellation is blocked
   and the user is informed that the task is in-flight.
4. **Given** a task completes, **When** the SSE completion event arrives,
   **Then** the corresponding transcript entry state updates to `completed`.

---

### User Story 6 — Pre-Fetch Cache & Context Hydration (Priority: P2)

The pre-fetch cache maintains an always-hot, in-memory snapshot of the current
environment state. It uses `watchdog` with inotify to monitor
`~/.hermes/memory` on jarlaxle for local file changes. For remote state on
artemis (Honcho workspace), it polls the Honcho HTTP API every 20–30 seconds.
On any change, the relevant portion of the cache is re-hydrated in the
background. The adapter calls `get_context()` synchronously to instantly
retrieve the current user profile, recent Honcho-synthesized memories, and
pending task states. The cache must be fully populated before the wake word
can trigger a session.

**Why this priority**: Context hydration directly impacts response quality but
is not required for basic pipeline operation. The adapter can function with
empty context initially.

**Independent Test**: Start the pre-fetch cache with valid Hermes memory path
and Honcho endpoint. Write a file to `~/.hermes/memory` and verify the cache
updates within 2 seconds. Mock a Honcho API response and verify polling
retrieves the data. Call `get_context()` and verify the merged context is
returned.

**Acceptance Scenarios**:

1. **Given** the cache is initialized, **When** a file changes in
   `~/.hermes/memory`, **Then** the relevant cache portion is re-hydrated
   within 2 seconds via inotify.
2. **Given** the Honcho API is reachable, **When** the polling interval
   elapses (20–30 seconds), **Then** the cache fetches and stores the latest
   Honcho state.
3. **Given** the cache is cold (just started), **When** the wake word triggers,
   **Then** session start is blocked until the cache completes initial
   hydration.
4. **Given** `get_context()` is called, **When** the cache is warm, **Then**
   it returns instantly (< 1 ms) with the merged context snapshot.
5. **Given** the Honcho API is unreachable, **When** the polling interval
   elapses, **Then** the cache logs a warning and retains the last known
   state — it does not crash or clear existing data.

---

### User Story 7 — Offline Handler & Morning Briefing (Priority: P3)

When the primary compute laptop (jarlaxle, running Qwen3 MoE via LiteLLM)
is shut down — currently between 1 AM and 5 AM — the offline handler
intercepts all traffic. It detects the offline window by performing a live
reachability check against the LiteLLM endpoint. Incoming commands are
classified into two categories: state-changing commands (fail fast, speak
warning via local TTS) and deferrable tasks (logged to a disk-backed JSON
queue). At 5 AM, the queue is flushed into a structured summary. On the
user's first interaction after coming online, Qwen reads the summary and
asks for confirmation before executing any queued items — the Morning
Briefing. The handler guards against Phantom Execution: it never silently
re-executes a state-changing command received while offline.

**Why this priority**: This is a quality-of-life feature for the 1–5 AM window.
The system is fully functional without it — requests simply fail during
downtime.

**Independent Test**: Mock the LiteLLM endpoint as unreachable. Send a
deferrable request and verify it is queued to disk. Send a state-changing
request and verify it is rejected with a spoken warning. Restore the endpoint
and verify the morning briefing summary is generated and presented on first
interaction.

**Acceptance Scenarios**:

1. **Given** the LiteLLM endpoint is unreachable, **When** the adapter receives
   a user transcript, **Then** the offline handler intercepts and classifies
   the command.
2. **Given** a state-changing command arrives while offline, **When** the
   handler classifies it, **Then** it immediately speaks a warning via TTS
   and does not queue the command.
3. **Given** a deferrable task arrives while offline, **When** the handler
   classifies it, **Then** it logs the task to a disk-backed JSON queue with
   timestamp and original transcript.
4. **Given** queued tasks exist at 5 AM, **When** the system comes online,
   **Then** the queue is flushed into a structured summary payload.
5. **Given** a morning briefing summary exists, **When** the user's first
   interaction occurs, **Then** Qwen reads the summary and asks for
   confirmation before executing any queued items.
6. **Given** a state-changing command was received while offline, **When** the
   system comes online, **Then** the command is never silently re-executed
   (Phantom Execution guard).

---

### Edge Cases

- What happens when the Pipecat pipeline crashes mid-conversation? The system
  MUST log the error, clean up resources, and restart the pipeline
  automatically within 5 seconds.
- What happens when multiple SSE task completion events arrive simultaneously?
  The bridge MUST process them sequentially, speaking each result with a brief
  pause between announcements.
- What happens when the Genie TTS server returns an error for a specific
  sentence? The GenieTTSService MUST skip that sentence, log the error, and
  continue with the next sentence in the queue.
- What happens when the pre-fetch cache watchdog inotify fails (e.g., inotify
  limit reached)? The cache MUST fall back to polling mode for local files
  and log a warning.
- What happens when Honcho on artemis is completely unreachable for hours? The
  cache MUST retain the last known state and continue operating with stale
  data rather than failing.
- What happens when a barge-in occurs during a system-initiated turn (SSE
  result injection)? The system MUST stop the injection, accept the user's
  input, and the SSE result is discarded.
- What happens when the offline handler's JSON queue file is corrupted? The
  handler MUST detect the corruption, log a warning, create a fresh queue
  file, and not crash.
- What happens when the `hermes_task_id` format is malformed in an SSE event?
  The bridge MUST reject the event, log the malformation, and continue
  listening.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST use Pipecat as the real-time voice pipeline
  framework with `FastAPIWebsocketTransport` for all audio I/O on the server.
- **FR-001b**: The system MUST provide an always-on satellite client (`satellite.py`)
  that runs on the edge node, handles local wake word detection, and streams PCM audio
  to/from the server over WebSockets.
- **FR-002**: The system MUST implement a custom Pipecat `TTSService` subclass
  (`GenieTTSService`) that synthesizes speech via the Genie TTS HTTP server
  (GPT-SoVITS V2Pro architecture) instead of any commercial provider.
- **FR-003**: The system MUST implement a central adapter orchestrator that
  receives transcribed text from Pipecat's STT output, constructs prompts
  using pre-fetch cache context, and routes to Qwen via the Hermes gateway.
- **FR-004**: The adapter MUST maintain a two-way event loop: simultaneously
  listening to Pipecat's STT output and incoming SSE events from Hermes for
  background task completions.
- **FR-005**: The adapter MUST generate a `hermes_task_id` (format:
  `task_YYYYMMDD_HHMMSS_XX`) for every Hermes dispatch and track its
  lifecycle through `pending`, `executing`, and `completed` states.
- **FR-006**: When a routing decision requires Hermes dispatch, the adapter
  MUST immediately stream a brief acknowledgment phrase to TTS before
  dispatching, filling silence while Hermes works.
- **FR-007**: On barge-in signal from Pipecat, the adapter MUST discard the
  pending response for the current task, mark the task as cancelled in the
  transcript, and accept new input cleanly.
- **FR-008**: The adapter MUST NOT attempt to kill in-flight tool calls on
  athrogate during barge-in — tool calls complete and results are discarded.
- **FR-009**: The transcript manager MUST maintain a hard-pruned 5–7 turn
  sliding window of recent conversation turns.
- **FR-010**: Every transcript turn involving Hermes dispatch MUST be tagged
  with `hermes_task_id` and `hermes_state` (`pending`, `executing`,
  `completed`, or `cancelled`).
- **FR-011**: The transcript manager MUST block cancellation of tasks currently
  tagged `executing`, informing the user that the task is already in-flight.
- **FR-012**: The pre-fetch cache MUST use `watchdog` with inotify to monitor
  `~/.hermes/memory` for local file changes and re-hydrate the cache within
  2 seconds of a change event.
- **FR-013**: The pre-fetch cache MUST poll the Honcho HTTP API on artemis
  every 20–30 seconds for remote state changes.
- **FR-014**: The pre-fetch cache MUST expose a synchronous `get_context()`
  method returning the current merged context snapshot with < 1 ms latency.
- **FR-015**: The pre-fetch cache MUST block session start until initial
  hydration is complete (cache-warm gate).
- **FR-016**: The offline handler MUST detect offline state by performing a
  live reachability check against the LiteLLM endpoint.
- **FR-017**: The offline handler MUST classify incoming commands as either
  state-changing (fail fast with TTS warning) or deferrable (queue to disk).
- **FR-018**: The offline handler MUST maintain a disk-backed JSON queue for
  deferrable tasks with timestamps and original transcripts.
- **FR-019**: At 5 AM (or when the system comes online), the offline handler
  MUST flush the queue into a structured Morning Briefing summary.
- **FR-020**: The offline handler MUST guard against Phantom Execution: never
  silently re-execute state-changing commands received while offline.
- **FR-021**: The pipecat bridge MUST maintain a persistent SSE listener
  against the Hermes gateway for task completion events.
- **FR-022**: When a task completion event matches a tracked `hermes_task_id`
  in `executing` state, the bridge MUST inject the response text into
  Pipecat's TTS queue as a system-initiated turn.
- **FR-023**: If a task completion arrives during an active user turn, the
  bridge MUST buffer the injection until the current turn's TTS finishes.
- **FR-024**: All services MUST communicate over Tailscale — use Tailscale IPs
  or hostnames for cross-machine calls, never assume LAN routing.
- **FR-025**: The GenieTTSService MUST sanitize TTS input: skip inputs with no
  alphanumeric content, ensure trailing punctuation, and handle casing
  requirements.
- **FR-026**: All configuration (Pipecat transport settings, Hermes URLs,
  Genie TTS URLs, Honcho endpoints, cache paths, offline schedule) MUST be
  loaded from a central `.env` file.
- **FR-027**: The adapter MUST reuse the existing `HermesClient` from
  `voice_satellite/gateway/hermes_client.py` for all Hermes communication.
- **FR-028**: The adapter MUST reuse the existing `GenieTTSClient` from
  `voice_satellite/tts/genie_client.py` as the backend for the
  `GenieTTSService`.

### Key Entities

- **AdapterSession**: Central state tracking the current pipeline state,
  active Hermes tasks, pre-fetch cache readiness, and offline mode status.
- **HermesTask**: A dispatched task with `hermes_task_id`, creation timestamp,
  current state, original transcript, and optional response text.
- **TranscriptTurn**: A single conversation turn with role (user/assistant/
  system), content, optional `hermes_task_id`, `hermes_state`, and timestamp.
- **ContextSnapshot**: The merged pre-fetch cache state containing user
  profile, recent Honcho memories, and pending task summaries.
- **OfflineQueueEntry**: A deferred task with timestamp, original transcript,
  command classification, and execution status.
- **MorningBriefing**: A structured summary of queued tasks generated at
  system wake, containing task list, timestamps, and confirmation prompts.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: End-to-end voice interaction latency (speech end to first audio
  response chunk) MUST be under 3 seconds when all backend services are
  running on jarlaxle over Tailscale.
- **SC-002**: Barge-in response time (user speech detected to TTS playback
  stopped) MUST be under 200 ms.
- **SC-003**: The GenieTTSService MUST produce first audio within 500 ms of
  receiving text input when the Genie server is warm.
- **SC-004**: The pre-fetch cache MUST serve `get_context()` calls in under
  1 ms from warm cache.
- **SC-005**: SSE task completion events MUST be spoken to the user within
  2 seconds of the event arriving at the bridge.
- **SC-006**: The adapter MUST run continuously for 24+ hours without memory
  leaks, task accumulation, or degraded responsiveness.
- **SC-007**: The offline handler MUST correctly classify and queue at least
  95% of deferrable commands without false positives on state-changing
  commands.
- **SC-008**: The morning briefing MUST be presented within 10 seconds of the
  user's first post-offline interaction.
- **SC-009**: The system MUST deploy on x86_64 (jarlaxle) from the same
  codebase without platform-specific modifications.

## Assumptions

- The Hermes gateway server is deployed and reachable over Tailscale on
  jarlaxle. The adapter does not embed the gateway.
- A Genie TTS server (GPT-SoVITS V2Pro) is deployed separately
  on jarlaxle. The adapter is a client; it does not embed the TTS engine.
- The Genie TTS server is incompatible with Genie's ONNX path — it is treated
  as a black-box HTTP endpoint.
- LiteLLM is running on jarlaxle routing to Qwen3 MoE. The LiteLLM endpoint
  URL is known and configurable.
- Honcho (with pgvector) is running on artemis and exposes an HTTP API
  accessible over Tailscale.
- athrogate runs the browser automation MCP server. The adapter does not
  communicate directly with athrogate — all tool dispatch goes through Hermes.
- kano (RTX 4050) is available for heavy inference but is not required for
  the adapter's core functionality.
- The existing `HermesClient`, `GenieTTSClient`, `effects.py`,
  and `sentence_splitter.py` modules are stable and can be imported from
  `voice_satellite/` without modification.
- Python 3.11+ is required.
- The always-on satellite client (`satellite.py`) replaces the browser frontend
  from `static/index.html` and uses the WebSocket protocol expected by
  `FastAPIWebsocketTransport`.
- openWakeWord with the custom "Renna" wake word model is used; faster-whisper
  handles STT.
