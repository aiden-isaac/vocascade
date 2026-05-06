# OpenClaw Voice Satellite - Agent Guidelines

## Architecture & Data Flow
- **Role**: Local voice interface. Captures speech -> transcribes locally -> queries Qwen via LiteLLM -> optionally routes to OpenClaw agents as a tool -> synthesizes TTS -> plays audio.
- **Frontend STT**: Browser runs Silero VAD v5 (WASM). It sends **raw 16-bit PCM bytes** to the backend over WebSocket.
- **Backend TTS**: Backend generates TTS (via `genie-tts`) and streams **base64-encoded raw PCM chunks** wrapped in JSON `{"type": "audio", "data": "...", "format": "pcm_s16le_mono", "sample_rate": 32000}`. *Note: Not WAV files.*
- **Concurrency**: `server.py` orchestrates the session via WebSockets. It gracefully cancels active background LLM/TTS asyncio tasks upon user interruption (barge-in).

## Repositories & Environments (CRITICAL)
This project requires **two separate virtual environments** running concurrently:
1. **Main Server (`/venv`)**: Runs `server.py` (FastAPI) on port 8001. Handles Whisper STT, routing, and WebSocket.
2. **Genie TTS Server (`/genie_model_reference/.venv`)**: Runs `server.py` on port 8000. Provides the GPT-SoVITS TTS service. 
Do not mix dependencies. Use `./start_servers.sh` to start both concurrently and `./stop_servers.sh` to cleanly kill them.

## Core Boundaries
- `server.py`: FastAPI WebSockets orchestrator and main entrypoint. Handles STT transcription, TTS scheduling, and LLM streaming logic.
- `voice_satellite/llm_router.py`: Local LLM router that decides whether to answer directly or invoke an OpenClaw agent as a tool. Returns a `RouterDecision`.
- `voice_satellite/openclaw_gateway.py`: WebSocket client connecting to OpenClaw.
- `voice_satellite/genie_tts.py`: Client connecting to the local Genie TTS service. Enforces exact chunk sizing (`bytes % 2 == 0`) to prevent WebAudio API static crashes.
- `static/index.html`: Vanilla HTML/JS frontend. No build step. Uses vendored `@ricky0123/vad-web@0.0.24` and `onnxruntime-web@1.14.0` to process Silero VAD v5 tensors.

## Testing & Verification
- **No `pytest`**: There is no standard test suite or test runner. Do not attempt to run `pytest`.
- **How to test**: Execute manual standalone verification scripts directly: `python test_openclaw_gateway.py`, `python test_llm_router.py`, etc. They use `assert` and `asyncio.run(main())`.
- **Environment Variables**: Local tests and the server require a `.env` file (e.g., `LITELLM_API_KEY`, `OPENCLAW_GATEWAY_TOKEN`). Never hardcode tokens.

## Implementation Quirks & Gotchas
- **VAD Limitations**: The vendored VAD setup in `index.html` requires `ort.env.wasm.proxy = false` and `ort.env.wasm.simd = true` to force execution onto the main thread and avoid dynamic `.mjs` import failures. Additionally, `onnxWASMBasePath: ASSET_PATH` must be passed to `MicVAD.new()` to prevent it from fetching `.wasm` files from `cdn.jsdelivr.net`.
- **Voice Configurations**: Multiple `genie-tts` voices (Fauna, Ordis) exist. Voice selection is managed entirely via `.env` overrides (`GENIE_CHARACTER_NAME`, `GENIE_ONNX_MODEL_DIR`, `GENIE_REFERENCE_AUDIO`, etc).
- **Hardware Acceleration**: `faster-whisper` is explicitly CPU-bound (`device="cpu"`, `compute_type="int8"`) to optimize for latency rather than throughput.
- **Hard Failures**: Errors (LLM timeouts, TTS crashes) must fail silently to the log. **Never** speak error stack traces aloud to the user.
- **No Wakeword**: The project currently has no wakeword implemented. Leave a `# TODO: wakeword` comment at integration points.