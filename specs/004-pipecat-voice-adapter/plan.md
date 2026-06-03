# Implementation Plan: Pipecat Voice Adapter

**Branch**: `pipecat` | **Date**: 2026-06-02 | **Spec**: [spec.md](spec.md)

## Summary

Replace the hand-rolled WebSocket/FastAPI audio pipeline in `voice_satellite/server.py` with a Pipecat-orchestrated real-time voice pipeline. Introduce a new `voice_adapter/` package containing six modules (adapter, tts_genie, pipecat_bridge, transcript_manager, pre_fetch_cache, offline_handler) that bridge Pipecat to the Hermes agent framework. The existing Hermes client, Genie TTS client, and audio effects chain are imported directly as stable dependencies. The old `voice_satellite/` pipeline is archived via git tag `v0-pre-pipecat` (clean break).

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: `pipecat-ai[websocket]` (FastAPIWebsocketTransport), `httpx` (Hermes/LiteLLM HTTP), `aiohttp` (Genie TTS), `watchdog` (inotify file monitoring), `faster-whisper` (STT), FastAPI/Uvicorn (server)

**Storage**: Local memory (transcript window, pre-fetch cache), disk-backed JSON (offline queue at `~/.hermes/offline_queue.json`), remote HTTP (Honcho API on artemis)

**Testing**: `pytest` — existing tests for reused components, new tests for adapter modules

**Target Platform**: Linux x86_64 (jarlaxle primary), potential aarch64 (Raspberry Pi 5)

**Project Type**: Real-time voice pipeline server

**Performance Goals**: End-to-end < 3s, barge-in < 200ms, TTS first audio < 500ms, `get_context()` < 1ms

**Constraints**: All services over Tailscale, no cloud TTS, no cloud STT, Genie TTS V2Pro (HTTP only, no ONNX path), single concurrent session

**Scale/Scope**: Single user, single device, 5–7 turn context window, 1–2 concurrent Hermes tasks

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Hardware Agnosticism | ✅ PASS | All config via `.env`, no hardcoded paths or device indices. Pipecat abstracts audio I/O. |
| II. Configuration-Driven | ✅ PASS | All endpoints, model paths, timeouts, polling intervals in `.env`. `.env.example` updated. |
| III. Modular Architecture | ✅ PASS | Each adapter module is independently importable/testable. Cross-module via typed interfaces. |
| IV. Async-First I/O | ✅ PASS | Pipecat is async-native. All I/O (HTTP, SSE, file watch) uses asyncio. |
| V. Documentation Discipline | ✅ PASS | All modules carry docstrings. Plan/spec/research created before implementation. |
| VI. Resilient Error Handling | ✅ PASS | Exponential backoff for Hermes/Genie/Honcho. Degraded modes for each service. Offline handler for downtime. |

## Proposed Architectural Changes

### 0. Always-On Satellite Client (`satellite.py`)

A lightweight daemon running on the edge node (e.g., Athrogate).
- **Wake Word Engine**: Uses `openwakeword` with a `.tflite` model for the "Renna" trigger phrase.
- **Audio I/O**: Continuous reading from the local microphone and raw PCM playback via local speakers.
- **Network**: Connects via WebSocket to the central FastAPI server over Tailscale.
- **State Machine**: Listens for wake word → opens WS → streams PCM → on timeout/close → returns to passive listening loop.

### 1. Pipecat Pipeline (`voice_adapter/adapter.py`)

The central orchestrator on Jarlaxle builds and runs the Pipecat pipeline:

```
FastAPIWebsocketTransport.input()
  → WhisperSTTService (faster-whisper)
      → AdapterProcessor (pre-flight, routing, Hermes dispatch)
        → OpenAILLMService (Hermes gateway, OpenAI-compatible)
          → GenieTTSService (Genie TTS HTTP)
            → FastAPIWebsocketTransport.output()
```

The `AdapterProcessor` is a custom `FrameProcessor` that:
- Receives `TranscriptionFrame` from STT
- Runs pre-flight check via `OfflineHandler`
- Pulls context from `PreFetchCache.get_context()`
- Constructs enriched prompt with context + transcript window
- For direct answers: passes through to LLM → TTS
- For Hermes dispatch: injects acknowledgment text frame, creates `HermesTask`, dispatches via `HermesClient`

### 2. Custom TTS (`voice_adapter/tts_genie.py`)

Subclass of `pipecat.services.tts_service.TTSService`:

```python
class GenieTTSService(TTSService):
    def __init__(self, genie_client: GenieTTSClient, **kwargs):
        super().__init__(**kwargs)
        self._genie = genie_client

    async def run_tts(self, text: str, context_id: str):
        # Sanitize (reuse GenieTTSClient's logic)
        async for chunk in self._genie.synthesize(text):
            yield AudioRawFrame(audio=chunk, sample_rate=32000, num_channels=1)
```

Delegates to the existing `GenieTTSClient` for HTTP calls, character loading, and degraded mode handling.

### 3. SSE Bridge (`voice_adapter/pipecat_bridge.py`)

Background asyncio task (not a Pipecat processor) that:
- Maintains persistent SSE connection to Hermes gateway
- Matches incoming task completion events to tracked `hermes_task_id`
- On match: injects response text into Pipecat's TTS pipeline via a shared queue
- Handles buffering when a user turn is active
- Reconnects with exponential backoff on disconnect

### 4. Transcript Manager (`voice_adapter/transcript_manager.py`)

Pure Python, no external dependencies:
- Sliding window of 5–7 `TranscriptTurn` objects
- Each turn: `role`, `content`, `hermes_task_id?`, `hermes_state?`, `timestamp`
- Methods: `append()`, `update_state(task_id, new_state)`, `get_window()`, `get_executing_tasks()`
- Cancellation guard: `can_cancel(task_id) -> bool` returns False if state is `executing`

### 5. Pre-Fetch Cache (`voice_adapter/pre_fetch_cache.py`)

Two data sources, unified interface:
- **Local (inotify)**: `watchdog.observers.Observer` watching `~/.hermes/memory/`
- **Remote (polling)**: `asyncio` task polling Honcho HTTP API every 20–30 seconds
- `get_context() -> ContextSnapshot`: synchronous read from in-memory dict
- `is_warm -> bool`: True when initial hydration complete
- Thread-safe via `threading.Lock` on the cache dict

### 6. Offline Handler (`voice_adapter/offline_handler.py`)

- `check_online() -> bool`: HTTP GET to LiteLLM `/health` with 2s timeout
- `classify_command(transcript) -> "state_changing" | "deferrable"`: keyword-based
- `queue_task(transcript)`: append to `~/.hermes/offline_queue.json`
- `flush_queue() -> MorningBriefing`: read queue, generate summary, clear file
- `is_offline_window() -> bool`: time-based check (1 AM–5 AM)

### 7. Configuration (`voice_adapter/config.py`)

Extends the existing pattern with adapter-specific fields:

```python
@dataclass(frozen=True)
class AdapterConfig:
    # Pipecat transport
    host: str
    port: int
    audio_in_sample_rate: int   # 16000
    audio_out_sample_rate: int  # 32000

    # Hermes gateway
    hermes_base_url: str
    hermes_api_key: str | None
    hermes_model: str
    hermes_sse_url: str         # NEW: SSE endpoint for task completions

    # Genie TTS
    tts_url: str
    tts_character_name: str
    tts_onnx_model_dir: str | None
    tts_reference_audio: str | None
    tts_reference_text: str | None
    tts_language: str

    # STT
    whisper_model: str
    whisper_language: str

    # Pre-fetch cache
    hermes_memory_path: str     # ~/.hermes/memory
    honcho_api_url: str         # Honcho on artemis
    honcho_poll_interval: int   # 20-30 seconds

    # Offline handler
    litellm_health_url: str     # LiteLLM /health endpoint
    offline_queue_path: str     # ~/.hermes/offline_queue.json
    offline_start_hour: int     # 1
    offline_end_hour: int       # 5

    # Feature flags
    skip_genie_init: bool
```

## Project Structure

### Documentation (this feature)

```text
specs/004-pipecat-voice-adapter/
├── spec.md
├── plan.md              # This file
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Generated by /speckit-tasks
```

### Source Code (repository root)

```text
satellite.py                 # Always-on edge client (wake word + audio I/O)
voice_adapter/
├── __init__.py
├── __main__.py              # Entry point: builds pipeline, runs uvicorn
├── adapter.py               # AdapterProcessor + pipeline builder
├── tts_genie.py             # GenieTTSService (Pipecat TTSService subclass)
├── pipecat_bridge.py        # SSE listener + async TTS injection
├── transcript_manager.py    # Tagged sliding window execution graph
├── pre_fetch_cache.py       # Watchdog + Honcho polling
├── offline_handler.py       # Downtime guardian + morning briefing
└── config.py                # AdapterConfig dataclass + .env loader

voice_satellite/             # Preserved for reusable components
├── gateway/
│   ├── base.py              # GatewayClient ABC (reused)
│   └── hermes_client.py     # HermesClient (reused)
├── tts/
│   ├── genie_client.py      # GenieTTSClient (reused as backend)
│   └── sentence_splitter.py # split_sentences (potentially reused)
├── audio/
│   └── effects.py           # apply_effect_chain (reused)
└── telemetry.py             # LatencyTracker (reused)

tests/
├── unit/
│   ├── test_adapter.py
│   ├── test_tts_genie.py
│   ├── test_pipecat_bridge.py
│   ├── test_transcript_manager.py
│   ├── test_pre_fetch_cache.py
│   ├── test_offline_handler.py
│   └── test_config_adapter.py
│   # Existing tests for reused components preserved:
│   ├── test_hermes_client.py
│   ├── test_genie_tts.py
│   ├── test_sentence_splitter.py
│   ├── test_effects.py
│   └── ...
└── integration/
    └── test_pipeline_roundtrip.py

static/
└── index.html               # Updated for Pipecat WebSocket protocol
```

**Structure Decision**: New `voice_adapter/` package at repo root, importing from `voice_satellite/` for reusable components. This preserves existing tests and avoids breaking imports while making the new adapter the primary entry point.

## Verification Plan

### Automated Tests

```bash
# Existing component tests (must still pass)
PYTHONPATH=. python -m pytest tests/unit/test_hermes_client.py
PYTHONPATH=. python -m pytest tests/unit/test_genie_tts.py
PYTHONPATH=. python -m pytest tests/unit/test_sentence_splitter.py

# New adapter tests
PYTHONPATH=. python -m pytest tests/unit/test_transcript_manager.py
PYTHONPATH=. python -m pytest tests/unit/test_offline_handler.py
PYTHONPATH=. python -m pytest tests/unit/test_pre_fetch_cache.py
PYTHONPATH=. python -m pytest tests/unit/test_tts_genie.py
PYTHONPATH=. python -m pytest tests/unit/test_adapter.py
PYTHONPATH=. python -m pytest tests/unit/test_pipecat_bridge.py

# Full suite
PYTHONPATH=. python -m pytest tests/
```

### Manual Verification

1. Start the adapter: `python -m voice_adapter`
2. Start the satellite client: `python satellite.py`
3. Speak "Renna" followed by a question → verify STT → Hermes → TTS round-trip
4. Test barge-in: interrupt mid-response → verify audio stops, new input accepted
5. Test offline mode: stop LiteLLM → send deferrable command → verify queue
6. Test SSE injection: dispatch Hermes task → wait for completion → verify spoken result
7. Test pre-fetch cache: modify `~/.hermes/memory/` file → verify cache update
