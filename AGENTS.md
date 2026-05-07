# OpenClaw Voice Satellite - Agent Guidelines

## Architecture & Data Flow
- **Role**: Local voice interface. Captures speech -> transcribes locally -> queries Qwen via LiteLLM -> optionally routes to OpenClaw agents as a tool -> synthesizes TTS -> plays audio.
- **Frontend STT**: Browser runs Silero VAD v5 (WASM). Sends **raw 16-bit PCM bytes** to the backend over WebSocket.
- **Backend TTS**: Backend generates TTS (via `genie-tts`) and streams **base64-encoded raw PCM chunks** wrapped in JSON `{"type": "audio", "data": "...", "format": "pcm_s16le_mono", "sample_rate": 32000}`. *Note: Not WAV files.*
- **Concurrency**: `server.py` orchestrates the session via WebSockets. Gracefully cancels active background LLM/TTS asyncio tasks upon user interruption (barge-in).

## Repositories & Environments (CRITICAL)
Two separate virtual environments running concurrently:
1. **Main Server (`venv/`)**: Runs `server.py` (FastAPI) on port 8001. Handles Whisper STT, routing, and WebSocket.
2. **Genie TTS Server (`genie_model_reference/.venv/`)**: Runs `server.py` on port 8000. Provides the GPT-SoVITS TTS service.
Use `./start_servers.sh` to start both and `./stop_servers.sh` to cleanly kill them. Do not mix dependencies.

## Core Boundaries
- `server.py`: FastAPI WebSockets orchestrator. Entry point for STT transcription, TTS scheduling, LLM streaming, and glitch audio distortion.
- `voice_satellite/llm_router.py`: Local LLM router. Decides whether to answer directly or invoke an OpenClaw agent as a tool. Returns a `RouterDecision`. Contains `COORDINATOR_SYSTEM_PROMPT`, `TOOL_RESULT_SYSTEM_PROMPT`, and `ORDIS_PERSONALITY_PROMPT`.
- `voice_satellite/openclaw_gateway.py`: WebSocket client connecting to OpenClaw.
- `voice_satellite/genie_tts.py`: Client connecting to the local Genie TTS service. Includes `iter_complete_sentences()` which splits text at sentence boundaries AND `<glitch>` tags for the distortion pipeline.
- `static/index.html`: Vanilla HTML/JS frontend. No build step. Uses vendored `@ricky0123/vad-web@0.0.24` and `onnxruntime-web@1.14.0` for Silero VAD v5.
- `glitch_tuner.py`: Interactive web utility for tuning glitch audio distortion parameters. Run with `python glitch_tuner.py` and visit `http://localhost:8002`.

## Testing & Verification
- **No `pytest`**: Do not attempt to run `pytest`.
- **How to test**: Execute manual standalone scripts: `python test_openclaw_gateway.py`, `python test_llm_router.py`, `python test_genie_tts_client.py`. They use `assert` and `asyncio.run(main())`.
- **Environment Variables**: Tests and server require a `.env` file. Never hardcode tokens.

## Implementation Quirks & Gotchas
- **VAD**: Requires `ort.env.wasm.proxy = false` and `ort.env.wasm.simd = true` in `index.html`. Pass `onnxWASMBasePath: ASSET_PATH` to `MicVAD.new()` to prevent CDN fetches.
- **Glitch Voice Pipeline** (Ordis only):
  - LLM wraps glitch outbursts in `<glitch>...</glitch>` tags via `ORDIS_PERSONALITY_PROMPT`.
  - `iter_complete_sentences()` in `genie_tts.py` splits text at `<glitch>` boundaries.
  - `server.py` `synthesize_sentence()` strips `<glitch>` tags, lowercases glitch text (prevents GPT-SoVITS spelling out words), removes non-alphanumeric fragments (prevents reference-text hallucination), and appends `.` if missing (forces stop token).
  - `apply_ordis_glitch()` in `server.py` applies randomized distortion: pitch shift (0.55-0.63), tremolo (8-14Hz), overdrive (2.5-4.5x), bitcrush (1-4 bits), stutter (70-120ms, 2-4 repeats).
- **Voice Configurations**: Multiple `genie-tts` voices (Fauna, Ordis). Managed via `.env` (`GENIE_CHARACTER_NAME`, `GENIE_ONNX_MODEL_DIR`, `GENIE_REFERENCE_AUDIO`, etc).
- **Hardware Acceleration**: `faster-whisper` is CPU-bound (`device="cpu"`, `compute_type="int8"`).
- **Hard Failures**: Errors (LLM timeouts, TTS crashes) must fail silently to the log. **Never** speak error stack traces aloud.
- **No Wakeword**: Leave `# TODO: wakeword` at integration points.
