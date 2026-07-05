# Implementation Plan: Latency Instrumentation

**Feature**: 003-latency-instrumentation
**Spec**: specs/003-latency-instrumentation/spec.md
**Created**: 2026-05-25

---

## Technical Context

### Technology Stack

- **Runtime**: Python 3.11+, asyncio
- **Timing primitive**: `time.perf_counter()` (monotonic, nanosecond-resolution, stdlib)
- **Logging**: Standard `logging` module — existing `voice_satellite.*` logger hierarchy
- **Dependencies**: None (stdlib only for `telemetry.py`)
- **Test framework**: pytest (already in use)

### Architecture Overview

The instrumentation is purely additive — no existing logic changes. A single new file (`voice_satellite/telemetry.py`) provides a `LatencyTracker` helper. Each pipeline file imports it and calls `tracker.record()` after its stage completes. The session correlation key originates in `server.py` and flows into the tracker at construction time.

```
server.py
  ├── wakeword received  → start e2e timer
  ├── handle_audio()
  │     ├── WhisperSTT.transcribe()       → LatencyTracker("stt", session)
  │     ├── HermesClient.send_transcript() → LatencyTracker("llm_first_token", session)
  │     ├── sentence_buffer flush logic   → LatencyTracker("sentence_buffer", session, sentence_index=N)
  │     └── GenieTTSClient.synthesize()   → LatencyTracker("tts_first_chunk", session)
  └── first audio chunk sent              → LatencyTracker("end_to_end", session)
```

### Files Changed

| File | Change type | Purpose |
|------|-------------|---------|
| `voice_satellite/telemetry.py` | **New** | `LatencyTracker` helper class |
| `voice_satellite/stt/whisper_stt.py` | Modified | Wrap `transcribe()` with STT timer |
| `voice_satellite/gateway/hermes_client.py` | Modified | Record `llm_first_token` timer inside `send_transcript()` |
| `voice_satellite/gateway/openclaw_client.py` | Modified | Record `llm_first_token` timer inside `send_transcript()` (parity) |
| `voice_satellite/tts/genie_client.py` | Modified | Record `tts_first_chunk` timer inside `synthesize()` |
| `voice_satellite/server.py` | Modified | Generate session ID, start/stop e2e timer, pass tracker into STT/LLM/TTS, record `sentence_buffer` |
| `tests/unit/test_telemetry.py` | **New** | Unit tests for `LatencyTracker` |

### Session ID Strategy

`server.py` generates a short UUID4 hex prefix (first 8 chars) when a WebSocket session is accepted. This ID is threaded into each `LatencyTracker` instance created during that session. Because only one session is active at a time (FR-007a), no conflicts are possible.

### Opt-out Flag

`LATENCY_LOGGING=false` (case-insensitive string comparison) is checked inside `LatencyTracker.record()` via `os.environ.get()`. The check is O(1) and has no side effects on any pipeline logic.

---

## Design Decisions

### Decision 1: LatencyTracker as a lightweight value object, not a context manager

**Chosen**: Simple class with `start()` / `record()` methods.

**Rationale**: Some timings span `async for` loops (LLM first token, TTS first chunk) where the end event is conditional (first non-empty token/chunk). A context manager would require `__aexit__` and additional state. The two-method API is more explicit and fits all use cases.

**Alternative considered**: `@contextmanager` — rejected because it doesn't naturally express "start timer here, stop only on first satisfying condition there".

### Decision 2: Session ID generated in server.py, not in each module

**Chosen**: `server.py` creates the ID when the WS connection is accepted and passes it to each `LatencyTracker`.

**Rationale**: The session ID is a server-level concept. Modules (STT, TTS, gateway) should not need to know about WS sessions. Passing the ID explicitly keeps modules decoupled.

### Decision 3: sentence_buffer timer measured in server.py, not sentence_splitter.py

**Chosen**: Timer logic stays in `server.py`'s `handle_audio()` where the buffer accumulation loop lives.

**Rationale**: `sentence_splitter.py` is a pure function with no side effects. Embedding timing there would couple a stateless utility to the logging infrastructure. The buffer timing is a property of the server-side orchestration, not the splitter.

### Decision 4: No changes to existing function signatures

**Chosen**: Pass `LatencyTracker` instances as local variables, not as extra arguments to existing methods.

**Rationale**: Changing signatures would break existing tests and is outside the "instrumentation only" constraint. The tracker is created, used, and discarded within the scope of each call site.

---

## Implementation Phases

### Phase 1 — Core telemetry helper + tests (Commit 1)

1. Create `voice_satellite/telemetry.py` with `LatencyTracker`.
2. Create `tests/unit/test_telemetry.py` with ≥3 unit tests.
3. Run full test suite to verify no regressions.

**Commit message**: `feat(telemetry): add LatencyTracker helper + unit tests`

### Phase 2 — Wire per-stage instrumentation (Commit 2)

1. Instrument `WhisperSTT.transcribe()` in `whisper_stt.py`.
2. Instrument `HermesClient.send_transcript()` in `hermes_client.py`.
3. Instrument `OpenClawClient.send_transcript()` in `openclaw_client.py` (parity).
4. Instrument `GenieTTSClient.synthesize()` in `genie_client.py`.
5. Wire `sentence_buffer`, `end_to_end`, and session ID into `server.py`.
6. Run full test suite to verify no regressions.

**Commit message**: `feat(instrumentation): wire per-stage latency logging into STT/LLM/TTS/e2e`

### Phase 3 — Spec/plan/tasks commit (Commit 3)

Commit the three spec files under `specs/003-latency-instrumentation/`.

**Commit message**: `docs(specs): add 003-latency-instrumentation spec/plan/tasks`

---

## Log Format Reference

```
[LATENCY] stage=stt duration_ms=612 session=abc123
[LATENCY] stage=llm_first_token duration_ms=834 session=abc123
[LATENCY] stage=sentence_buffer sentence_index=0 duration_ms=120 session=abc123
[LATENCY] stage=tts_first_chunk duration_ms=310 session=abc123
[LATENCY] stage=end_to_end duration_ms=1876 session=abc123
```

All values are integers (milliseconds, rounded). Extra key-value pairs (e.g. `sentence_index`) appear between `duration_ms` and `session`.

---

## Risk & Mitigation

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Timer overhead affects measured latency | Low | `perf_counter()` is sub-microsecond; negligible vs. ms-scale pipeline stages |
| Session ID not available at STT/TTS call site | Low | ID is generated at WS accept and passed explicitly; all call sites are within `handle_audio()` scope |
| Existing tests mock STT/TTS and won't trigger new log lines | Low | Tests don't assert absence of log lines; no change needed |
| openclaw_client.py has different streaming model | Medium | Reviewed source; same `async for token` pattern — instrumentation is identical |
