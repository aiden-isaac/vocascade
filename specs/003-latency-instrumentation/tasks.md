# Tasks: Latency Instrumentation

**Feature**: 003-latency-instrumentation
**Plan**: specs/003-latency-instrumentation/plan.md
**Created**: 2026-05-25

---

## Phase 1 — Setup

- [x] T001 Create `specs/003-latency-instrumentation/` directory with spec.md, plan.md, and tasks.md

---

## Phase 2 — Foundational: LatencyTracker helper

- [x] T002 Create `voice_satellite/telemetry.py` with `LatencyTracker` class
  - Class accepts `stage: str`, `session: str`, and optional extra kwargs at construction
  - `start()` records `time.perf_counter()` as `_t0`
  - `record(**extra)` computes `duration_ms = int((perf_counter() - _t0) * 1000)`, then emits `[LATENCY] stage=<stage> duration_ms=<ms> <extra k=v pairs> session=<session>` at `logging.INFO` via the `voice_satellite.telemetry` logger
  - If `os.environ.get("LATENCY_LOGGING", "").lower() == "false"`, `record()` is a no-op
  - No imports outside stdlib (`logging`, `os`, `time`)

- [x] T003 [P] Create `tests/unit/test_telemetry.py` with unit tests for `LatencyTracker`
  - Test 1: `start()` + `record()` emits a log line matching `[LATENCY] stage=stt duration_ms=\d+ session=test123` (use `caplog` or `unittest.mock`)
  - Test 2: `record(sentence_index=0)` includes `sentence_index=0` in the log line before `session=`
  - Test 3: With `LATENCY_LOGGING=false` in env, `record()` emits no log lines (assert logger not called / caplog is empty)
  - Test 4: Measured `duration_ms` is ≥ 0 and ≤ 5000 for a zero-sleep scenario (sanity check)

---

## Phase 3 — User Story 1: Per-stage instrumentation

### US1: STT stage

- [ ] T004 [US1] Instrument `WhisperSTT.transcribe()` in `voice_satellite/stt/whisper_stt.py`
  - Accept optional `session: str = ""` keyword argument in `transcribe()`
  - Before `async with self.lock`: create `tracker = LatencyTracker("stt", session)` and call `tracker.start()`
  - After `await loop.run_in_executor(...)` returns: call `tracker.record()`
  - Import `LatencyTracker` from `voice_satellite.telemetry`

### US1: LLM first-token stage (Hermes)

- [ ] T005 [US1] Instrument `HermesClient.send_transcript()` in `voice_satellite/gateway/hermes_client.py`
  - Accept optional `session: str = ""` keyword argument in `send_transcript()`
  - Before the `async with self.client.stream(...)` block: create `tracker = LatencyTracker("llm_first_token", session)` and call `tracker.start()`
  - After the first non-empty `content` is yielded: call `tracker.record()` (set a `_first_token_recorded` flag so `record()` is only called once)
  - Import `LatencyTracker` from `voice_satellite.telemetry`

### US1: LLM first-token stage (OpenClaw — parity)

- [ ] T006 [P] [US1] Instrument `OpenClawClient.send_transcript()` in `voice_satellite/gateway/openclaw_client.py`
  - Same pattern as T005: optional `session` kwarg, timer starts before POST/stream, `record()` on first non-empty token
  - Import `LatencyTracker` from `voice_satellite.telemetry`

### US1: TTS first-chunk stage

- [ ] T007 [US1] Instrument `GenieTTSClient.synthesize()` in `voice_satellite/tts/genie_client.py`
  - At the top of the method body (after sanitisation guards, before the HTTP request): create `tracker = LatencyTracker("tts_first_chunk", session)` and call `tracker.start()`
  - Accept optional `session: str = ""` keyword argument in `synthesize()`
  - After the first `yield bytes(...)` inside the `async for chunk` loop: call `tracker.record()` (one-shot flag)
  - Import `LatencyTracker` from `voice_satellite.telemetry`

### US1: sentence_buffer + end_to_end stages (server.py)

- [ ] T008 [US1] Wire session ID, sentence_buffer timer, and end_to_end timer in `voice_satellite/server.py`
  - Generate `_session_id = uuid.uuid4().hex[:8]` when the WebSocket connection is accepted (after `await websocket.accept()`)
  - In `handle_audio()`: immediately after `session.state = SessionState.TRANSCRIBING`, start `e2e_tracker = LatencyTracker("end_to_end", _session_id)` and `e2e_tracker.start()`
  - Pass `session=_session_id` to `stt_client.transcribe()`, `gateway_client.send_transcript()`, and `tts_client.synthesize()` calls
  - In the sentence-flush loop: before dispatching each sentence to `speak_text_to_tts()`, record a sentence_buffer line: `LatencyTracker("sentence_buffer", _session_id).start()` at the moment the sentence is formed; call `.record(sentence_index=N)` immediately (the buffer wait time is measured from when the sentence was first accumulated, not from dispatch)
    - Simplification: stamp the buffer entry time when each sentence is added to `sentence_buffer`, and record elapsed time when it is flushed; use a `sentence_buffer_start` dict keyed by sentence index
  - In `speak_text_to_tts()` or at the point in `handle_audio` where the first audio JSON is `send`-ed: call `e2e_tracker.record()` (one-shot flag)
  - Import `LatencyTracker` and `uuid` (already imported) from respective modules

---

## Phase 4 — Polish & Validation

- [ ] T009 Run full test suite: `PYTHONPATH=. python -m pytest tests/` — all tests must pass
- [ ] T010 Verify log format manually or via integration assertion: confirm five distinct `[LATENCY]` stage names appear in output when satellite handles a test utterance

---

## Commit Plan

| Commit | Scope | Message |
|--------|-------|---------|
| 1 | T002, T003 | `feat(telemetry): add LatencyTracker helper + unit tests` |
| 2 | T004–T008 | `feat(instrumentation): wire per-stage latency logging into STT/LLM/TTS/e2e` |
| 3 | T001 (spec files) | `docs(specs): add 003-latency-instrumentation spec/plan/tasks` |

---

## Dependency Graph

```
T001 (spec files — independent)
T002 (telemetry.py) → T003 (tests) → T004, T005, T006, T007, T008
T004, T005, T006, T007 → T008 (server wiring)
T008 → T009 (test suite)
T009 → T010 (manual/integration validation)
```

## Parallel Execution Opportunities

- T003 (write test file) can be done in parallel with T002 after the API is decided
- T006 (OpenClaw parity) can be done in parallel with T005 (Hermes)
- T004, T005, T006, T007 can be done in parallel with each other once T002 is complete
