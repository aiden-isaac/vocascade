# OpenClaw Voice Satellite - Agent Guidelines

## Architecture & Data Flow

- **Role**: Local voice interface. Captures speech -> transcribes locally -> queries Qwen via LiteLLM -> optionally routes to OpenClaw agents as a tool -> synthesizes TTS -> plays audio.
- **Frontend**: Browser runs Silero VAD v5 (WASM) and OpenWakeWord (ONNX). Sends **raw 16-bit PCM bytes** to the backend over WebSocket. In passive mode, only the wakeword model runs; VAD speech is discarded until a wakeword event is detected.
- **Backend TTS**: Backend generates TTS (via `genie-tts`) and streams **base64-encoded raw PCM chunks** wrapped in JSON `{"type": "audio", "data": "...", "format": "pcm_s16le_mono", "sample_rate": 32000, "word_offset": N}`. Not WAV files.
- **Concurrency**: `server.py` orchestrates the session via a `ConversationSession` state machine. Gracefully cancels active LLM/TTS asyncio tasks on barge-in; partial utterance context is preserved for the next turn.

## Repositories & Environments (CRITICAL)

Two separate virtual environments running concurrently:
1. **Main Server (`venv/`)**: Runs `server.py` (FastAPI) on port 8001. Handles Whisper STT, routing, and WebSocket.
2. **Genie TTS Server (`genie_model_reference/.venv/`)**: Runs `server.py` on port 8000. Provides the GPT-SoVITS TTS service.

Use `./start_servers.sh` to start both and `./stop_servers.sh` to cleanly kill them. Do not mix dependencies.

## Module Map

| File | Role |
|------|------|
| `server.py` | FastAPI WebSocket orchestrator. STT, TTS scheduling, LLM streaming, glitch distortion, session wiring. |
| `voice_satellite/session.py` | `ConversationSession` state machine. States: passive_listening, acknowledging, active_listening, transcribing, thinking, filler_speaking, speaking, interrupted. Manages silence timer and barge-in word tracking. |
| `voice_satellite/task_tracker.py` | `TaskTracker` for async OpenClaw background tasks. Fires `on_complete` callback when an agent finishes; proactively wakes a passive session. |
| `voice_satellite/filler_engine.py` | `FillerEngine` loads `static/fillers/<category>/*.pcm` at startup into RAM for zero-latency filler playback. |
| `voice_satellite/llm_router.py` | `LLMRouter`. Routes to Qwen or OpenClaw. Actions: `answer`, `openclaw`, `check_tasks`, `conversation_end`. Contains `COORDINATOR_SYSTEM_PROMPT`, `TOOL_RESULT_SYSTEM_PROMPT`, `ORDIS_PERSONALITY_PROMPT`. |
| `voice_satellite/genie_tts.py` | Genie TTS client. `iter_complete_sentences()` splits at sentence boundaries and `<glitch>` tag boundaries. |
| `voice_satellite/openclaw_gateway.py` | WebSocket client to the OpenClaw gateway. |
| `static/index.html` | Vanilla HTML/JS frontend. No build step. Uses vendored `@ricky0123/vad-web@0.0.24` and `onnxruntime-web@1.14.0`. |
| `static/wakeword/model.onnx` | OpenWakeWord ONNX model loaded in-browser. Descriptor at `static/wakeword/model.json`. |
| `static/fillers/` | Pre-rendered PCM filler audio. Subdirs: `thinking/`, `working/`, `slow_task/`, `acknowledge/`, `signoff/`. |
| `scripts/generate_fillers.py` | Batch-renders filler phrases via Genie TTS to `static/fillers/`. |
| `scripts/glitch_tuner.py` | Interactive web UI for tuning Ordis glitch distortion parameters (port 8002). |
| `genie/server.py` | Minimal Genie TTS server entry point (`genie_tts.start_server`). Used when `GENIE_DIR` is not overridden. |

## Testing & Verification

- **No `pytest`**: Do not attempt to run `pytest`.
- **How to test**: Execute manual standalone scripts. They use `assert` and `asyncio.run(main())`.
  ```bash
  python tests/test_session.py
  python tests/test_filler_engine.py
  python tests/test_task_tracker.py
  python tests/test_llm_router.py
  python tests/test_openclaw_gateway.py
  python tests/test_genie_tts_client.py
  ```
- **Environment Variables**: Tests and server require a `.env` file. Never hardcode tokens.
- **Startup smoke test**: `./start_servers.sh` then `./stop_servers.sh`.

## Implementation Quirks & Gotchas

- **VAD**: Requires `ort.env.wasm.proxy = false` and `ort.env.wasm.simd = true` in `index.html`. Pass `onnxWASMBasePath: ASSET_PATH` to `MicVAD.new()` to prevent CDN fetches.
- **Wakeword**: Frontend loads `static/wakeword/model.onnx` via `ort.InferenceSession`. On detection, sends `{"type": "wakeword"}` to backend. Backend plays an `acknowledge/` filler and transitions session to `active_listening`.
- **Glitch Voice Pipeline** (Ordis only):
  - LLM wraps glitch outbursts in `<glitch>...</glitch>` tags via `ORDIS_PERSONALITY_PROMPT`.
  - `iter_complete_sentences()` in `genie_tts.py` splits text at `<glitch>` boundaries.
  - `server.py` `synthesize_sentence()` strips `<glitch>` tags, lowercases glitch text (prevents GPT-SoVITS spelling out words), removes non-alphanumeric fragments (prevents reference-text hallucination), and appends `.` if missing (forces stop token).
  - `apply_ordis_glitch()` in `server.py` applies randomized distortion: pitch shift (0.55-0.63), tremolo (8-14Hz), overdrive (2.5-4.5x), bitcrush (1-4 bits), stutter (70-120ms, 2-4 repeats).
- **Barge-in Context**: The frontend reports `{"type": "playback_progress", "words_played": N}` just before sending `{"type": "interrupt"}`. The backend uses `words_played` to reconstruct the partial utterance the user heard and injects it into LLM history as `"... [interrupted by user]"`.
- **Filler Race**: In `answer_with_qwen_session()`, an `asyncio.Task` waits `FILLER_THRESHOLD_SECS` (default 2.0s, env-configurable). If the real TTS starts before the timer, the filler task is cancelled. If the timer fires first, a pre-rendered filler PCM is sent immediately.
- **OpenClaw Task Completion**: `TaskTracker.spawn()` wraps the gateway coroutine in an `asyncio.Task`. On completion, the `on_complete` callback in `server.py` either speaks a notification (if session is active) or proactively wakes the session (if passive).
- **Silence Timeout**: Default 30s, configurable via UI slider (10-120s). Persisted in `localStorage`. Can be updated at runtime via `{"type": "set_timeout", "seconds": N}` WebSocket message.
- **Voice Configurations**: Multiple `genie-tts` voices (Fauna, Ordis). Managed via `.env` (`GENIE_CHARACTER_NAME`, `GENIE_ONNX_MODEL_DIR`, `GENIE_REFERENCE_AUDIO`, etc.).
- **Hardware Acceleration**: `faster-whisper` is CPU-bound (`device="cpu"`, `compute_type="int8"`).
- **Hard Failures**: Errors (LLM timeouts, TTS crashes) must fail silently to the log. Never speak error stack traces aloud.
