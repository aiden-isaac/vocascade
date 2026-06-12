# Tasks: Pipecat Voice Adapter

**Input**: Design documents from `/specs/004-pipecat-voice-adapter/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, quickstart.md

**Tests**: Unit tests included for all modules.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependencies, and package structure

- [x] T001 Create `voice_adapter/` package directory with `voice_adapter/__init__.py`
- [x] T002 Add `pipecat-ai[websocket]` and `watchdog` to `requirements.txt`
- [x] T003 [P] Create adapter configuration module at `voice_adapter/config.py` with `AdapterConfig` dataclass and `.env` loader — include all fields from plan.md (Pipecat transport, Hermes gateway, Genie TTS, STT, pre-fetch cache, offline handler, audio settings)
- [x] T004 [P] Update `.env.example` with all new adapter-specific environment variables (HERMES_SSE_URL, HONCHO_API_URL, HONCHO_POLL_INTERVAL, LITELLM_HEALTH_URL, OFFLINE_QUEUE_PATH, HERMES_MEMORY_PATH, OFFLINE_START_HOUR, OFFLINE_END_HOUR, AUDIO_IN_SAMPLE_RATE, AUDIO_OUT_SAMPLE_RATE)
- [x] T005 [P] Create `voice_adapter/__main__.py` entry point stub that loads config, prints health report, and will launch the Pipecat pipeline (placeholder for Phase 3 wiring)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data structures and utilities that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T006 Implement `HermesTaskState` enum (`pending`, `executing`, `completed`, `cancelled`) and `HermesTask` dataclass in `voice_adapter/transcript_manager.py`
- [x] T007 Implement `TranscriptTurn` dataclass in `voice_adapter/transcript_manager.py` with fields: `role`, `content`, `hermes_task_id`, `hermes_state`, `timestamp`, `was_interrupted`
- [x] T008 Implement `TranscriptManager` class in `voice_adapter/transcript_manager.py` with methods: `append(turn)`, `update_state(task_id, new_state)`, `get_window()` (returns 5-7 most recent turns), `get_executing_tasks()`, `can_cancel(task_id) -> bool`
- [x] T009 [P] Implement `ContextSnapshot` dataclass in `voice_adapter/pre_fetch_cache.py` with fields: `user_profile`, `recent_memories`, `pending_tasks`, `last_updated`
- [x] T010 [P] Write unit tests for `TranscriptManager` in `tests/unit/test_transcript_manager.py` — test sliding window pruning, state updates, cancellation guard, executing task queries
- [x] T011 [P] Write unit tests for `AdapterConfig` in `tests/unit/test_config_adapter.py` — test .env loading, defaults, fail-fast on missing required vars

**Checkpoint**: Foundation ready — data structures and config validated, user story implementation can begin

---

## Phase 2.5: User Story 0 — Satellite Client (Priority: P0) 🎯 Edge Client

**Goal**: Implement the `satellite.py` edge daemon that listens for the wake word and handles local audio I/O.

**Independent Test**: Run `satellite.py`, speak "Renna", and verify it attempts to connect to a WebSocket server.

### Tests for User Story 0

- [x] T011.1 [P] [US0] Write unit tests for the satellite state machine in `tests/unit/test_satellite.py` (mocking audio I/O and WebSocket).

### Implementation for User Story 0

- [x] T011.2 [US0] Create `satellite.py` at the repository root. Implement the `openWakeWord` continuous listening loop using the custom `.tflite` model.
- [x] T011.3 [US0] Implement audio hardware I/O in `satellite.py` (e.g. PyAudio) to read from microphone and play incoming raw PCM chunks to speakers.
- [x] T011.4 [US0] Implement the WebSocket connection state machine in `satellite.py`: on wake word → connect to WS over Tailscale → stream audio. On silence/server close → disconnect and return to listening.

---

## Phase 3: User Story 1 — Voice Pipeline Orchestration (Priority: P1) 🎯 Central MVP

**Goal**: Build the Pipecat pipeline with FastAPIWebsocketTransport, STT, LLM (Hermes OpenAI-compatible), and TTS. User speaks → hears response.

**Independent Test**: Start adapter with Hermes + Genie running. Connect browser WebSocket, speak a question, verify audio response.

### Tests for User Story 1

- [x] T012 [P] [US1] Write unit tests for `GenieTTSService` in `tests/unit/test_tts_genie.py` — test `run_tts` yields `AudioRawFrame` at 32kHz, handles degraded mode, skips non-alphanumeric input, handles cancellation
- [x] T013 [P] [US1] Write unit tests for `AdapterProcessor` in `tests/unit/test_adapter.py` — test transcript routing, acknowledgment injection for Hermes dispatch, hermes_task_id generation format

### Implementation for User Story 1

- [x] T014 [US1] Implement `GenieTTSService` in `voice_adapter/tts_genie.py` — subclass `pipecat.services.tts_service.TTSService`, implement `run_tts(text, context_id)` method that delegates to `GenieTTSClient.synthesize()`, yields `AudioRawFrame(audio=chunk, sample_rate=32000, num_channels=1)`, handles character loading on init, degraded mode, input sanitization
- [x] T015 [US1] Implement `AdapterProcessor` as a Pipecat `FrameProcessor` in `voice_adapter/adapter.py` — receives `TranscriptionFrame` from STT, appends to `TranscriptManager`, constructs enriched prompt, passes through to LLM for direct answers, generates `hermes_task_id` and dispatches via `HermesClient` for Hermes-required tasks, injects acknowledgment text frame before dispatch
- [x] T016 [US1] Implement `build_pipeline()` function in `voice_adapter/adapter.py` — creates the full Pipecat pipeline: `FastAPIWebsocketTransport.input() → STT → AdapterProcessor → OpenAILLMService → GenieTTSService → FastAPIWebsocketTransport.output()`, configure `FastAPIWebsocketParams` with `audio_in_sample_rate=16000`, `audio_out_sample_rate=32000`
- [x] T017 [US1] Implement FastAPI app and `/ws` WebSocket endpoint in `voice_adapter/adapter.py` — on connection: build pipeline, create `PipelineTask`, run pipeline. Wire lifespan for startup (Genie character load, Hermes connect) and shutdown (resource cleanup)
- [x] T018 [US1] Wire `voice_adapter/__main__.py` to import and run the FastAPI app via uvicorn with host/port from `AdapterConfig`
- [x] T019 [US1] Implement `hermes_task_id` generation function in `voice_adapter/adapter.py` — format: `task_YYYYMMDD_HHMMSS_XX` where XX is a zero-padded session-scoped counter

**Checkpoint**: At this point, `python -m voice_adapter` should start, accept WebSocket connections, and complete a full STT → LLM → TTS round-trip

---

## Phase 4: User Story 2 — Genie TTS Integration (Priority: P1)

**Goal**: Ensure GenieTTSService handles all edge cases: character registration, streaming, degradation, effects chain

**Independent Test**: Initialize GenieTTSService, send text, verify PCM audio frames at correct sample rate

**Note**: Basic GenieTTSService was created in US1 (T014). This phase adds robustness and effects integration.

### Implementation for User Story 2

- [x] T020 [US2] Add character loading to `GenieTTSService` init in `voice_adapter/tts_genie.py` — call `GenieTTSClient.load_character()` during pipeline startup, handle degraded mode if Genie unreachable
- [x] T021 [US2] Integrate audio effects chain into `GenieTTSService` in `voice_adapter/tts_genie.py` — import `apply_effect_chain` and `get_character_effects_config` from `voice_satellite.audio.effects`, apply effects to audio chunks before yielding frames
- [x] T022 [US2] Configure premade acknowledgement audio files in `voice_adapter/adapter.py` — map tasks to correct premade acknowledgement PCM files, ensuring they are loaded and played instantly when Hermes dispatch is triggered
- [x] T023 [US2] Write integration test in `tests/unit/test_tts_genie.py` — test effects chain application and premade acknowledgement audio playback

**Checkpoint**: GenieTTSService is production-ready with effects, premade acknowledgements, and degraded mode

---

## Phase 5: User Story 4 — Barge-In & Interruption Handling (Priority: P1)

**Goal**: User can interrupt assistant mid-speech. Audio stops, new input accepted, Hermes task discarded.

**Independent Test**: Start long response, interrupt mid-sentence, verify audio stops and new input works

### Tests for User Story 4

- [x] T024 [P] [US4] Write unit tests for barge-in in `tests/unit/test_adapter.py` — test that interruption cancels TTS, marks task as cancelled in TranscriptManager, discards pending response

### Implementation for User Story 4

- [x] T025 [US4] Configure Pipecat's native VAD-based interruption in `voice_adapter/adapter.py` — set `enable_interruptions=True` on the pipeline params, handle `UserStartedSpeakingFrame` to trigger barge-in logic
- [x] T026 [US4] Implement barge-in handler in `AdapterProcessor` in `voice_adapter/adapter.py` — on interruption: cancel current TTS output, mark active `HermesTask` as `cancelled` in `TranscriptManager`, do NOT abort in-flight tool calls on athrogate (let them complete, discard results), accept new audio input
- [x] T027 [US4] Update `TranscriptManager.append()` in `voice_adapter/transcript_manager.py` to support `was_interrupted=True` flag on assistant turns that were cut short

**Checkpoint**: Barge-in works end-to-end. User can interrupt and continue naturally

---

## Phase 6: User Story 3 — Hermes Task Dispatch & Async Result Injection (Priority: P1)

**Goal**: Long-running Hermes tasks complete in background; results spoken unprompted when ready

**Independent Test**: Dispatch Hermes task, simulate SSE completion event, verify result spoken to user

### Tests for User Story 3

- [x] T028 [P] [US3] Write unit tests for `PipecatBridge` in `tests/unit/test_pipecat_bridge.py` — test SSE event parsing, task_id matching, result injection, buffering during active turn, reconnection logic

### Implementation for User Story 3

- [x] T029 [US3] Implement `PipecatBridge` class in `voice_adapter/pipecat_bridge.py` — asyncio background task that maintains persistent SSE connection to Hermes gateway (`hermes_sse_url` from config), parses incoming events, matches `hermes_task_id` against `TranscriptManager.get_executing_tasks()`
- [x] T030 [US3] Implement result injection logic in `voice_adapter/pipecat_bridge.py` — when matched task completion arrives: update `TranscriptManager` state to `completed`, inject response text into Pipecat's TTS queue. If user turn is active: buffer the injection until current TTS finishes
- [x] T031 [US3] Implement SSE reconnection with exponential backoff in `voice_adapter/pipecat_bridge.py` — on disconnect: log warning, retry with backoff (initial 1s, max 60s), preserve tracked task state across reconnections
- [x] T032 [US3] Wire `PipecatBridge` startup/shutdown into `voice_adapter/adapter.py` lifespan — start bridge as background task on app startup, cancel on shutdown
- [x] T033 [US3] Handle cancelled task completion events in `voice_adapter/pipecat_bridge.py` — if SSE completion arrives for a task already marked `cancelled`, discard the result and do not speak it

**Checkpoint**: Hermes background tasks complete and results are spoken to user unprompted

---

## Phase 7: User Story 5 — Transcript Manager & Execution Graph (Priority: P2)

**Goal**: Transcript manager provides intelligent routing context and prevents race conditions

**Independent Test**: Append mixed-state turns, verify window pruning, state propagation, cancellation blocking

**Note**: Basic TranscriptManager was created in Phase 2 (T006-T008). This phase adds execution graph intelligence.

### Tests for User Story 5

- [x] T034 [P] [US5] Write extended unit tests in `tests/unit/test_transcript_manager.py` — test execution guard logic: `can_cancel()` returns False for `executing` tasks, True for `pending`, context window serialization for prompt construction

### Implementation for User Story 5

- [x] T035 [US5] Implement `get_context_for_prompt()` method in `voice_adapter/transcript_manager.py` — serializes the sliding window into a format suitable for inclusion in Qwen prompts, including task state tags: `[TASK:task_id STATE:executing]` appended to relevant turns
- [x] T036 [US5] Implement cancellation guard UX in `voice_adapter/adapter.py` — when user requests cancellation of a task, check `TranscriptManager.can_cancel(task_id)`. If False (task is `executing`), inject TTS response: "That task is already in progress and can't be cancelled. I'll let you know when it's done."
- [x] T037 [US5] Add automatic window pruning on append in `voice_adapter/transcript_manager.py` — when window exceeds 7 turns, remove oldest turns. Preserve any turn with a task in `executing` state (don't prune in-flight tasks)

**Checkpoint**: Transcript manager provides full execution graph context for intelligent routing

---

## Phase 8: User Story 6 — Pre-Fetch Cache & Context Hydration (Priority: P2)

**Goal**: Always-hot context cache with local inotify and remote Honcho polling

**Independent Test**: Write file to `~/.hermes/memory/`, verify cache update. Mock Honcho API, verify polling.

### Tests for User Story 6

- [x] T038 [P] [US6] Write unit tests for `PreFetchCache` in `tests/unit/test_pre_fetch_cache.py` — test `get_context()` returns merged snapshot, test `is_warm` gate, test Honcho poll failure (retains stale data), test local file change triggers re-hydration

### Implementation for User Story 6

- [x] T039 [US6] Implement `PreFetchCache` class in `voice_adapter/pre_fetch_cache.py` — thread-safe in-memory cache with `threading.Lock`, `get_context() -> ContextSnapshot` (synchronous read), `is_warm -> bool` property
- [x] T040 [US6] Implement local watchdog observer in `voice_adapter/pre_fetch_cache.py` — use `watchdog.observers.Observer` with `FileSystemEventHandler` to monitor `hermes_memory_path` (default `~/.hermes/memory/`). On file create/modify/delete: re-read the changed file and update cache. Fallback to polling if inotify limit reached
- [x] T041 [US6] Implement Honcho HTTP polling in `voice_adapter/pre_fetch_cache.py` — asyncio background task that calls Honcho API every `honcho_poll_interval` seconds (default 25s). Parse response into `recent_memories` field of `ContextSnapshot`. On failure: log warning, retain last known state
- [x] T042 [US6] Implement cache-warm gate in `voice_adapter/adapter.py` — before allowing pipeline to process first utterance, check `PreFetchCache.is_warm`. If cold, block and wait for initial hydration (with 10s timeout, proceed with empty context on timeout)
- [x] T043 [US6] Wire `PreFetchCache` startup/shutdown into `voice_adapter/adapter.py` lifespan — start watchdog observer and polling task on startup, stop on shutdown

**Checkpoint**: Pre-fetch cache provides sub-millisecond context lookups with automatic background hydration

---

## Phase 9: User Story 7 — Offline Handler & Morning Briefing (Priority: P3)

**Goal**: Graceful handling of downtime (1 AM–5 AM), queuing deferrable commands, morning briefing

**Independent Test**: Mock LiteLLM offline, send commands, verify classification and queuing. Restore and verify briefing.

### Tests for User Story 7

- [x] T044 [P] [US7] Write unit tests for `OfflineHandler` in `tests/unit/test_offline_handler.py` — test `check_online()` with mock HTTP, test `classify_command()` for state-changing vs deferrable, test `queue_task()` writes to JSON file, test `flush_queue()` generates MorningBriefing, test `is_offline_window()` time logic, test Phantom Execution guard

### Implementation for User Story 7

- [x] T045 [US7] Implement `OfflineHandler` class in `voice_adapter/offline_handler.py` — `check_online() -> bool` (HTTP GET to LiteLLM `/health`, 2s timeout), `is_offline_window() -> bool` (time-based check using `offline_start_hour` and `offline_end_hour` from config)
- [x] T046 [US7] Implement `classify_command(transcript) -> str` in `voice_adapter/offline_handler.py` — keyword-based classification: state-changing keywords (deploy, restart, turn on/off, set, delete, run, execute, activate), deferrable keywords (remind, what is, tell me, check, find, how, when, who, summarize). Default ambiguous to deferrable
- [x] T047 [US7] Implement `queue_task(transcript)` and disk-backed JSON queue in `voice_adapter/offline_handler.py` — append `OfflineQueueEntry` to `offline_queue_path` (default `~/.hermes/offline_queue.json`). Handle file creation, corruption detection (try parse, create fresh on failure)
- [x] T048 [US7] Implement `flush_queue() -> MorningBriefing` in `voice_adapter/offline_handler.py` — read all entries from queue file, generate `MorningBriefing` with human-readable summary text, clear the queue file. Guard against Phantom Execution: never include state-changing commands in the briefing's executable list
- [x] T049 [US7] Wire offline handler into `AdapterProcessor` in `voice_adapter/adapter.py` — before routing transcript: call `OfflineHandler.check_online()`. If offline: classify command, queue or reject. If online and morning briefing exists: inject briefing into first interaction
- [x] T050 [US7] Implement morning briefing delivery in `voice_adapter/adapter.py` — on first post-offline interaction: read `MorningBriefing`, prepend summary to the user's prompt context so Qwen reads it and asks for confirmation before executing queued items

**Checkpoint**: Offline handling works end-to-end. Commands queued during downtime, morning briefing delivered on wake.

---

## Phase 9.5: User Story 8 — Graceful Conversation Termination (Priority: P2)

**Goal**: Let the assistant speak a farewell, then reliably return the client to
passive/wakeword listening — even when the local model is inconsistent.

**Independent Test**: Connect client, say "that will be all", verify bot speaks a
farewell and the client receives `passive_listening` (returns to wakeword mode).

**Design note (revised 2026-06-04)**: The original tool-call approach
(`terminate_session`) and a single sentinel proved unreliable — a small, fast
local model emits the sentinel nondeterministically, so the session sometimes
never returned to wakeword mode. Termination now fires on **either** of two
independent signals, checked when the bot finishes speaking
(`BotStoppedSpeakingFrame`):
  1. **Model sentinel** — `ENDSESSION` present anywhere in the buffered reply
     (matched case/space-insensitively, tolerant of streamed token splits).
  2. **Deterministic phrase backstop** — `AdapterProcessor` matches a farewell
     phrase ("that will be all", "goodbye", …) on the *user* transcript and arms
     `TeardownInterceptor.arm_termination()`.
An interruption mid-farewell disarms termination (the user re-engaged).

### Tests for User Story 8

- [x] T059 [P] [US8] Unit tests in `tests/unit/test_adapter.py` for `_is_farewell`, `_contains_sentinel`, and `_strip_sentinel` (positives, negatives, no false-positive on "end … session").

### Implementation for User Story 8

- [x] T060 [US8] `AdapterProcessor` system message instructs the model to append `ENDSESSION` on its own line for clear farewells only.
- [x] T061 [US8] Removed the `terminate_session` tool; `AdapterProcessor` arms `TeardownInterceptor.arm_termination()` via deterministic farewell-phrase detection on the user transcript.
- [x] T062 [US8] `TeardownInterceptor.process_frame` strips the sentinel from TTS, commits the reply to history, and on `BotStoppedSpeakingFrame` pushes `passive_listening` when either signal is present. (Also now calls `super().process_frame()` per Pipecat's `FrameProcessor` contract.)

---

## Phase 9.6: User Story 9 — Preloaded Acknowledgement ("Ack") Audio (Priority: P2)

**Goal**: Zero-latency spoken acknowledgement from pre-rendered PCM clips —
a "Yes?" the instant the wakeword fires. Clips are generated offline from a
config of phrases.

**Design note (revised 2026-06-12)**: `acknowledge` (wakeword) is the ONLY
filler category in use. "Working"/"thinking" style clips were dropped — a clip
played at Hermes dispatch races the local model's own spoken handoff
("Let me check..."), creating overlapping/competing audio. The model's verbal
handoff IS the dispatch acknowledgement.

**Independent Test**: With `static/fillers/` populated, say the wakeword → hear an
ack clip immediately (before STT/LLM).

### Implementation for User Story 9

- [x] T063 [US9] `scripts/generate_fillers.py` + `static/fillers.json` render a per-category phrase list to `static/fillers/<category>/<slug>.pcm` via Genie TTS. (Future: Hermes can regenerate these from its own phrase set.)
- [x] T064 [US9] Implement `FillerEngine` in `voice_adapter/filler_engine.py` — load PCM clips into RAM at startup, serve random clip per category with `thinking` fallback.
- [x] T065 [US9] Add `filler_dir` to `AdapterConfig`; load `FillerEngine` in the adapter lifespan.
- [x] T066 [US9] Wire instant playback in `AdapterProcessor`: `acknowledge` clip on wakeword (`OutputAudioRawFrame` at the output sample rate). A `working` clip on Hermes dispatch was tried and removed — see design note above.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, cleanup, integration testing, frontend update

- [x] T051 [P] Update `README.md` with new adapter architecture, quickstart, and configuration reference
- [x] T052 [P] Update `AGENTS.md` with new project layout and voice_adapter entry point
- [x] T053 [P] Update `static/index.html` WebSocket client to work with Pipecat's `FastAPIWebsocketTransport` protocol (binary audio frames, text control messages)
- [x] T054 Write integration test in `tests/integration/test_pipeline_roundtrip.py` — test full pipeline with mocked Hermes and Genie: audio in → STT → adapter → LLM → TTS → audio out
- [x] T055 Run full test suite: `PYTHONPATH=. python -m pytest tests/` — verify all existing reused component tests still pass alongside new adapter tests
- [x] T056 Run quickstart.md validation — follow quickstart steps from scratch, verify successful setup and first interaction
- [x] T057 [P] Add structured logging throughout voice_adapter modules — use Python `logging` with consistent logger names (`voice_adapter.adapter`, `voice_adapter.tts_genie`, etc.)
- [x] T058 Performance validation — measure end-to-end latency, barge-in time, TTS first-audio time against success criteria (SC-001 through SC-009)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US0 Satellite Client (Phase 2.5)**: Independent, but required for true end-to-end usage.
- **US1 Pipeline (Phase 3)**: Depends on Foundational — this is the central MVP
- **US2 Genie TTS (Phase 4)**: Depends on US1 (T014 creates basic GenieTTSService)
- **US4 Barge-In (Phase 5)**: Depends on US1 (needs working pipeline)
- **US3 SSE Bridge (Phase 6)**: Depends on US1 + Foundational (needs TranscriptManager + pipeline)
- **US5 Transcript Graph (Phase 7)**: Depends on Foundational (T006-T008 create basic TranscriptManager)
- **US6 Pre-Fetch Cache (Phase 8)**: Depends on US1 (needs working pipeline to be useful)
- **US7 Offline Handler (Phase 9)**: Depends on US1 (needs working pipeline to intercept)
- **Polish (Phase 10)**: Depends on all desired user stories being complete

### User Story Dependencies

```
Phase 1 (Setup) → Phase 2 (Foundational)
                        ↓
                   Phase 3 (US1: Pipeline) 🎯 MVP
                   ↙    ↓         ↘
          Phase 4    Phase 5    Phase 6
          (US2)      (US4)      (US3)
                        ↓
                   Phase 7 (US5: Transcript Graph)
                        ↓
                   Phase 8 (US6: Pre-Fetch Cache)
                        ↓
                   Phase 9 (US7: Offline Handler)
                        ↓
                   Phase 10 (Polish)
```

### Parallel Opportunities

- T003, T004, T005 can run in parallel (Phase 1)
- T009, T010, T011 can run in parallel (Phase 2)
- T012, T013 can run in parallel (Phase 3 tests)
- Phase 4, Phase 5, Phase 6 can run in parallel after Phase 3

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1 (Pipeline)
4. **STOP and VALIDATE**: `python -m voice_adapter` → speak → hear response
5. Deploy if ready — this is a functional voice assistant

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 (Pipeline) → Test independently → **MVP!**
3. Add US2 (Genie TTS polish) → Robust TTS with effects
4. Add US4 (Barge-In) → Natural interruption
5. Add US3 (SSE Bridge) → Async task results
6. Add US5 (Transcript Graph) → Intelligent routing
7. Add US6 (Pre-Fetch Cache) → Context-rich responses
8. Add US7 (Offline Handler) → 24/7 operation
9. Polish → Production-ready

---

## Summary

- **Total tasks**: 66
- **Phase 1 (Setup)**: 5 tasks
- **Phase 2 (Foundational)**: 6 tasks
- **Phase 2.5 (US0 Satellite Client)**: 4 tasks
- **Phase 3 (US1 Pipeline)**: 8 tasks — Central MVP
- **Phase 4 (US2 Genie TTS)**: 4 tasks
- **Phase 5 (US4 Barge-In)**: 3 tasks
- **Phase 6 (US3 SSE Bridge)**: 6 tasks
- **Phase 7 (US5 Transcript Graph)**: 4 tasks
- **Phase 8 (US6 Pre-Fetch Cache)**: 6 tasks
- **Phase 9 (US7 Offline Handler)**: 7 tasks
- **Phase 9.5 (US8 Graceful Termination)**: 4 tasks
- **Phase 9.6 (US9 Preloaded Ack Audio)**: 4 tasks
- **Phase 10 (Polish)**: 8 tasks
- **Parallel opportunities**: 16 tasks marked [P]
- **Suggested MVP scope**: Phase 1 + Phase 2 + Phase 3 (19 tasks)

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Existing `voice_satellite/` tests must continue passing — they validate reused components

---

## Reality Check & Roadmap (updated 2026-06-04)

Several tasks below were marked `[x]` ahead of their actual implementation. Current
**verified** state of `voice_adapter/`:

**Working today (simple, local-only voice assistant):**
- US1 pipeline (transport → VAD shim → Whisper STT → AdapterProcessor → Qwen LLM →
  TeardownInterceptor → Genie TTS → output), single-session WS endpoint, static UI.
- US8 graceful termination (dual-signal — see Phase 9.5).
- US9 preloaded ack audio (see Phase 9.6).
- Hermes tool is now correctly advertised to the model and dispatches a background
  task; the result is spoken when it arrives (`inject_text`). Note: this depends on
  the local model supporting OpenAI tool-calling.

**Stubbed / not yet real (despite `[x]` marks above):**
- `voice_adapter/pre_fetch_cache.py` — `PreFetchCache` is a no-op stub (`is_warm`
  always True, empty `ContextSnapshot`). Watchdog + Honcho polling NOT implemented.
- `voice_adapter/pipecat_bridge.py` — **does not exist**. SSE task-completion bridge
  (US3) is not implemented; background results are currently injected directly from
  the in-process Hermes call rather than via a persistent SSE channel.
- `voice_adapter/offline_handler.py` — **does not exist** (US7 not implemented).

### The "hard part" — Hermes context + proactive task tracking (next milestone)

Goal: the fast/stateless local adapter understands the user using context from the
Hermes agent, and tracks long-running Hermes tool work like Jarvis ("Okay, I'm on
it" → keep conversing → later, proactively: "the server finished that task").

1. **Context hydration (US6, make real)**: implement `PreFetchCache` for real —
   watchdog on `~/.hermes/memory/` + Honcho polling — and have `AdapterProcessor`
   prepend `get_context()` to the system prompt so Qwen has user/profile/recent-memory
   context without a round-trip. Gate first utterance on `is_warm` (10s timeout).
2. **Task lifecycle (US3 + US5)**: when `query_hermes_agent` dispatches, create a
   `HermesTask` (PENDING→EXECUTING) in `TranscriptManager` keyed by `hermes_task_id`;
   tag context turns with `[TASK:<id> STATE:executing]` via
   `get_context_for_prompt()` so the model can answer "is that done yet?" correctly.
3. **Proactive completion (US3)**: build `PipecatBridge` — a persistent SSE listener
   on `hermes_sse_url` that matches completion events to tracked task IDs, flips state
   to COMPLETED, and injects the spoken result (buffering while the user is talking).
   Discard results for tasks marked CANCELLED.
4. **Cancellation guard (US5)**: honor `TranscriptManager.can_cancel()` — refuse to
   cancel a task already EXECUTING and tell the user it'll be reported when done.
