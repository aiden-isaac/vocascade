# Feature Specification: Latency Instrumentation

**Feature Branch**: `feat/latency-instrumentation`

**Created**: 2026-05-25

**Status**: Draft

## Overview

The Voice Satellite pipeline passes audio through several sequential stages before the user hears a response. Without timing data, there is no objective way to identify which stage is the bottleneck or to validate whether the end-to-end latency target in SC-003 (< 3 seconds) is being met in production.

This feature adds lightweight, non-invasive timing instrumentation to each pipeline stage so that operators can observe real latency numbers in the server log without any external tooling or additional dependencies.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Operator observes per-stage latency in logs (Priority: P1)

An operator runs the satellite and speaks a phrase. Immediately after the first audio chunk is played back, they can grep the server log for `[LATENCY]` and see one structured line per pipeline stage, all sharing the same `session=` correlation key, so they can reconstruct the timeline for any given turn.

**Why this priority**: Without observable latency data, no performance optimisation is possible and SC-003 cannot be verified.

**Independent Test**: Start the satellite (or a test harness that stubs STT/LLM/TTS), speak a phrase, and assert that the expected `[LATENCY]` log lines appear with plausible `duration_ms` values.

**Acceptance Scenarios**:

1. **Given** the satellite processes one voice turn, **When** the turn completes, **Then** the log contains exactly five structured `[LATENCY]` lines with keys `stt`, `llm_first_token`, `sentence_buffer`, `tts_first_chunk`, and `end_to_end`, all sharing the same `session=` value.
2. **Given** multiple consecutive turns from the same WebSocket session, **When** each turn completes, **Then** all `[LATENCY]` lines for that session share the same `session=` correlation key, making the log groupable per session.
3. **Given** the environment variable `LATENCY_LOGGING=false` is set, **When** the satellite runs, **Then** no `[LATENCY]` lines appear in the log for any stage.
4. **Given** a barge-in interrupts a turn mid-stream, **When** the interrupted turn's timing is recorded, **Then** only the stages that actually completed before the interrupt emit a `[LATENCY]` line; no partial/incomplete measurements are logged.

---

### User Story 2 — Developer validates LatencyTracker in isolation (Priority: P1)

A developer writes or runs unit tests for the `LatencyTracker` helper to confirm that it records duration correctly, formats the log line as specified, and respects the `LATENCY_LOGGING=false` opt-out without any server infrastructure.

**Why this priority**: The helper is the single point of instrumentation logic. It must be correct before it is wired into the pipeline.

**Independent Test**: Run `PYTHONPATH=. python -m pytest tests/unit/test_telemetry.py` without starting the satellite.

**Acceptance Scenarios**:

1. **Given** a `LatencyTracker` is started and then stopped, **When** `record()` is called, **Then** the measured duration is within ±10 ms of the wall-clock elapsed time.
2. **Given** extra keyword arguments are passed to `record()` (e.g. `sentence_index=0`), **When** the log line is emitted, **Then** those key-value pairs appear verbatim in the log line after `duration_ms`.
3. **Given** `LATENCY_LOGGING=false` is set in the environment, **When** `record()` is called, **Then** no log line is emitted at any log level.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST emit a structured log line at INFO level for each of the following pipeline stages after each stage completes: `stt`, `llm_first_token`, `sentence_buffer`, `tts_first_chunk`, `end_to_end`.
- **FR-002**: Each log line MUST follow the format: `[LATENCY] stage=<name> duration_ms=<integer> session=<id> [optional key=value pairs]`.
- **FR-003**: All timing MUST use `time.perf_counter()` for monotonic, high-resolution measurement. No wall-clock (`time.time()`) is permitted in timing paths.
- **FR-004**: All timing logic MUST be encapsulated in a single `LatencyTracker` helper class in `voice_satellite/telemetry.py`. No pipeline file may call `time.perf_counter()` directly.
- **FR-005**: The `LatencyTracker` MUST accept a `session` identifier at construction time and include it in every emitted log line, enabling multi-turn log correlation.
- **FR-006**: Setting the environment variable `LATENCY_LOGGING=false` (case-insensitive) MUST silence all `[LATENCY]` log output at runtime without requiring a server restart. No other configuration changes or new required variables are introduced.
- **FR-007**: The end-to-end timer MUST start when the wakeword event is received on the WebSocket and stop when the first base64 PCM audio chunk is sent back on the WebSocket.
- **FR-008**: The `sentence_buffer` measurement MUST record how long each sentence waited in the accumulation buffer before being dispatched to TTS, along with a `sentence_index` counter identifying which sentence in the turn it is.
- **FR-009**: The `llm_first_token` measurement MUST record the time from when the POST request is sent to the gateway until the first non-empty token is yielded.
- **FR-010**: The implementation MUST NOT alter any logic, branching, or data flow in any instrumented file. Instrumentation is additive only.
- **FR-011**: All existing unit tests MUST continue to pass without modification after instrumentation is added.
- **FR-012**: At least one unit test for `LatencyTracker` MUST be added in `tests/unit/test_telemetry.py`.

### Key Entities

- **LatencyTracker**: A lightweight helper that wraps `time.perf_counter()`, holds a `stage` name and `session` ID, and formats/emits a structured `[LATENCY]` log line when `record()` is called.
- **Pipeline Stage**: A named checkpoint in the voice processing pipeline (STT, LLM first token, sentence buffer, TTS first chunk, end-to-end) that is timed from a defined start event to a defined end event.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After speaking a phrase to a running satellite, five `[LATENCY]` log lines appear in the server log within 10 seconds, all sharing the same `session=` value.
- **SC-002**: The `LatencyTracker` unit test suite passes in under 1 second with zero external dependencies (stdlib only).
- **SC-003**: Setting `LATENCY_LOGGING=false` produces zero `[LATENCY]` lines in the log for any subsequent turn, verifiable by log inspection.
- **SC-004**: No existing test breaks as a result of adding instrumentation (100% pass rate on `PYTHONPATH=. python -m pytest tests/`).
- **SC-005**: The `telemetry.py` module has no imports outside the Python standard library.

---

## Assumptions

- The satellite runs a single WebSocket session at a time (FR-007a from spec-001), so using the WebSocket session ID as the correlation key is unambiguous.
- Log output is directed to stdout/stderr by the existing Python `logging` configuration; no new log handlers are needed.
- The `sentence_buffer` stage is measured per-sentence within a single LLM response stream, not per-turn; therefore multiple `sentence_buffer` log lines per turn are expected.
- Latency values are always non-negative; no defensive clamping to zero is required.
- The opt-out flag `LATENCY_LOGGING=false` is evaluated once per `record()` call (environment lookup), which is acceptable given the low call rate.
