# OpenClaw Voice Satellite

A fully local, low-latency voice interface for interacting with OpenClaw agents.

## Architecture

```
Browser (VAD/STT) → WebSocket → server.py (FastAPI :8001) → Whisper STT → LLM Router → TTS (Genie :8000) → Browser
```

- **Frontend STT**: Silero VAD v5 runs in-browser via WASM. Sends **raw 16-bit PCM bytes** to backend over WebSocket.
- **Backend STT**: `faster-whisper` (CPU, INT8, `tiny.en`) transcribes PCM to text.
- **LLM Routing**: LiteLLM routes to Qwen. Decides whether to answer directly or invoke an OpenClaw agent as a tool.
- **Backend TTS**: `genie-tts` (GPT-SoVITS) on port 8000. Returns **base64-encoded raw PCM** (`pcm_s16le_mono`, 32kHz). *Not WAV files.*
- **Glitch Voice**: Ordis's `<glitch>` outbursts are distorted in real-time via numpy (pitch shift, tremolo, bitcrush, stutter) before being sent to the browser.
- **Barge-in**: Server cancels active LLM/TTS asyncio tasks on user interruption.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Two separate virtual environments are required:
1. **Main Server** (`venv/`): FastAPI on port 8001
2. **Genie TTS** (`genie_model_reference/.venv/`): GPT-SoVITS on port 8000

## Running

```bash
./start_servers.sh   # Starts both servers concurrently
./stop_servers.sh    # Kills both cleanly
```

Access the UI at `http://0.0.0.0:8001`.

## Configuration

Create a `.env` file with:
```ini
LITELLM_API_KEY=your_key
OPENCLAW_GATEWAY_TOKEN=your_token
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
LITELLM_URL=https://llm.frizzt.com/v1
LLM_MODEL=qwen-moe-coder-fast

# Ordis voice configuration
GENIE_CHARACTER_NAME=ordis
GENIE_ONNX_MODEL_DIR=/home/aiden/voice-satellite/genie_model_reference/genie-ordis/export
GENIE_REFERENCE_AUDIO=/home/aiden/voice-satellite/ordis_voice/ordis_ref.wav
GENIE_REFERENCE_TEXT="What? A parity drift? How is that posible? Have you executed your diagnosic presets?"
GENIE_LANGUAGE=en
```

## Glitch Voice (Ordis)

When `GENIE_CHARACTER_NAME=ordis`, the system applies a real-time audio distortion pipeline to `<glitch>`-tagged text segments:

1. **Tagging**: The LLM wraps glitch outbursts in `<glitch>...</glitch>` tags.
2. **Chunking**: Text is split at `<glitch>` boundaries so each segment is synthesized separately.
3. **Distortion**: Numpy applies randomized pitch shift (0.55-0.63), tremolo (8-14Hz), overdrive (2.5-4.5x), bitcrush (1-4 bits), and stutter (70-120ms, 2-4 repeats) to glitch audio chunks.
4. **Sanitization**: Glitch text is lowercased before TTS to prevent the model from spelling out words. Non-alphanumeric fragments are dropped to prevent reference-text hallucination.

### Glitch Tuner Utility

Test and tune glitch parameters interactively:
```bash
source venv/bin/activate
python glitch_tuner.py
```
Visit `http://localhost:8002` for a web UI with sliders for all distortion parameters.

## Testing

No `pytest`. Run standalone scripts directly:
```bash
python test_llm_router.py
python test_openclaw_gateway.py
python test_genie_tts_client.py
```

## Known Constraints

- **VAD**: Fixed in v0.0.24. Requires `ort.env.wasm.proxy = false` and `ort.env.wasm.simd = true` in `static/index.html`.
- **TTS Quirks**: GPT-SoVITS hallucinates reference text on empty/punctuated input. Always ensure glitch text has alphanumeric content and ends with punctuation.
- **No Wakeword**: Currently none. Leave `# TODO: wakeword` at integration points.
- **No Error Speech**: Never speak error stack traces to the user. Log errors silently.
