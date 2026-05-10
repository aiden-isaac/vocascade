# OpenClaw Voice Satellite

A fully local, low-latency voice interface for interacting with OpenClaw agents.

## Architecture

```
Browser (VAD + Wakeword ONNX)
  -> WebSocket ->
    server.py (FastAPI :8001)
      -> Whisper STT (faster-whisper, CPU)
      -> LLMRouter (Qwen via LiteLLM)
          -> FillerEngine (pre-rendered PCM, 2s race)
          -> TaskTracker (async OpenClaw background tasks)
      -> Genie TTS (:8000, GPT-SoVITS)
          -> Ordis glitch distortion (numpy)
  -> WebSocket ->
Browser (AudioContext PCM playback)
```

- **Frontend**: Silero VAD v5 (WASM) + OpenWakeWord ONNX run in-browser. In passive mode only the wakeword model is active; speech is ignored until wakeword fires. In active mode all VAD audio goes to the backend.
- **Backend STT**: `faster-whisper` (CPU, INT8, `tiny.en`) transcribes PCM to text.
- **LLM Routing**: `LLMRouter` routes to Qwen for direct answers or spawns OpenClaw agent calls. Actions: `answer`, `openclaw`, `check_tasks`, `conversation_end`.
- **Backend TTS**: `genie-tts` (GPT-SoVITS) on port 8000. Returns base64-encoded raw PCM (`pcm_s16le_mono`, 32 kHz). Not WAV files.
- **Glitch Voice**: Ordis `<glitch>` outbursts are distorted in real-time via numpy (pitch shift, tremolo, bitcrush, stutter) before being sent to the browser.
- **Barge-in**: Frontend reports word playback progress; backend reconstructs the partial utterance heard by the user and injects it into LLM history before cancelling the generation task.
- **Fillers**: Pre-rendered PCM audio files loaded at startup. A 2-second asyncio timer races the real response; if the response is slow, a filler plays immediately.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Two separate virtual environments are required:
1. **Main Server** (`venv/`): FastAPI on port 8001.
2. **Genie TTS** (`genie_model_reference/.venv/`): GPT-SoVITS on port 8000.

## Running

```bash
./start_servers.sh   # starts both servers
./stop_servers.sh    # kills both cleanly
```

Access the UI at `http://localhost:8001`.

## Configuration

Create a `.env` file:

```ini
LITELLM_API_KEY=your_key
OPENCLAW_GATEWAY_TOKEN=your_token
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
LITELLM_URL=https://llm.frizzt.com/v1
LLM_MODEL=qwen-moe-coder-fast

# Ordis voice
GENIE_CHARACTER_NAME=ordis
GENIE_ONNX_MODEL_DIR=/path/to/genie-ordis/export
GENIE_REFERENCE_AUDIO=/path/to/ordis_ref.wav
GENIE_REFERENCE_TEXT="What? A parity drift? How is that possible?"
GENIE_LANGUAGE=en

# Optional tuning
WHISPER_MODEL=tiny.en
WHISPER_LANGUAGE=en
FILLER_THRESHOLD_SECS=2.0
```

## Wakeword

The frontend loads `static/wakeword/model.onnx` via ONNX Runtime WASM. Place your OpenWakeWord ONNX model there and update `static/wakeword/model.json`:

```json
{
  "file": "model.onnx",
  "name": "Hey Ordis",
  "sample_rate": 16000,
  "threshold": 0.5
}
```

## Filler Audio

Pre-render filler phrases in the active voice (requires servers running):

```bash
python generate_fillers.py
```

Output goes to `static/fillers/<category>/<phrase>.pcm`. Categories: `thinking`, `working`, `slow_task`, `acknowledge`, `signoff`.

## Glitch Voice (Ordis)

When `GENIE_CHARACTER_NAME=ordis`, the LLM wraps glitch outbursts in `<glitch>...</glitch>` tags:

1. **Tagging**: `ORDIS_PERSONALITY_PROMPT` instructs the LLM to use `<glitch>` tags.
2. **Splitting**: `iter_complete_sentences()` splits at `<glitch>` boundaries so each segment synthesizes separately.
3. **Sanitization**: Glitch text is lowercased (prevents GPT-SoVITS spelling out words) and non-alphanumeric fragments are dropped (prevents reference-text hallucination).
4. **Distortion**: `apply_ordis_glitch()` applies randomized pitch shift (0.55-0.63), tremolo (8-14 Hz), overdrive (2.5-4.5x), bitcrush (1-4 bits), stutter (70-120 ms, 2-4 repeats).

### Glitch Tuner

Tune distortion parameters interactively:

```bash
source venv/bin/activate
python glitch_tuner.py
# Visit http://localhost:8002
```

## Testing

No `pytest`. Run standalone scripts directly:

```bash
python test_session.py
python test_filler_engine.py
python test_task_tracker.py
python test_llm_router.py
python test_openclaw_gateway.py
python test_genie_tts_client.py
```

## WebSocket Protocol

### Client -> Server

| Message | When | Payload |
|---------|------|---------|
| `binary` | VAD speech end (active mode) | Raw 16-bit PCM bytes |
| `{"type":"wakeword"}` | Wakeword ONNX fires | -- |
| `{"type":"interrupt"}` | User speaks mid-response | -- |
| `{"type":"playback_progress","words_played":N}` | Just before interrupt | Word offset for context |
| `{"type":"set_timeout","seconds":N}` | UI slider changed | Silence timeout update |

### Server -> Client

| Message | When |
|---------|------|
| `{"type":"status","state":"..."}` | State transitions |
| `{"type":"audio","data":"...","word_offset":N,"sample_rate":32000}` | TTS audio chunk |
| `{"type":"flush_audio"}` | Server-initiated audio stop |
| `{"type":"audio_end"}` | TTS pipeline complete |
| `{"type":"transcript","text":"..."}` | Whisper result |
| `{"type":"decision","action":"..."}` | Router decision |
| `{"type":"task_complete","task_id":"...","summary":"..."}` | Background agent done |
| `{"type":"assistant_delta","text":"..."}` | LLM streaming chunk |
| `{"type":"assistant_response","text":"..."}` | Full turn response |
| `{"type":"error","message":"..."}` | Pipeline failure |

## Known Constraints

- **VAD**: Requires `ort.env.wasm.proxy = false` and `ort.env.wasm.simd = true`. WASM assets must be served locally (no CDN fetches).
- **TTS Quirks**: GPT-SoVITS hallucinates reference text on empty or punctuation-only input. Glitch text must have alphanumeric content and end with punctuation.
- **No Error Speech**: Never speak error stack traces. Log silently.
- **CPU Only**: `faster-whisper` runs on CPU (`device="cpu"`, `compute_type="int8"`). No GPU required.
