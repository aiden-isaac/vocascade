# OpenClaw Voice Satellite

A fully local, low-latency voice interface for interacting with OpenClaw agents.

## Features
- **Real-time VAD**: Uses a tuned Silero VAD (v0.0.24) running entirely in the browser via WASM to detect speech and barge-ins without server roundtrips. Loads all WASM assets locally (no CDN required).
- **Optimized Local STT**: Uses `faster-whisper` configured for extreme low latency (INT8 compute on CPU, no previous context conditioning).
- **Streaming LLM**: Routes queries using a local LLM via LiteLLM. Responses are streamed chunk-by-chunk to reduce time-to-first-byte.
- **Pipelined TTS**: Connects to a local `genie-tts` (GPT-SoVITS) instance. Supports swappable voice models (e.g., Fauna, Ordis). As the LLM completes sentences, they are immediately queued and synthesized into raw PCM audio for playback.
- **True Interruption (Barge-in)**: The server orchestrates audio through asynchronous background tasks. When the user starts speaking, an interrupt signal cancels the LLM generation and flushes the TTS queue instantly.

## Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Ensure you have a `.env` file with `LITELLM_API_KEY` and `OPENCLAW_GATEWAY_TOKEN`.

You can configure which voice to use by setting the `GENIE_CHARACTER_NAME` and `GENIE_ONNX_MODEL_DIR` variables in your `.env` file:
```ini
GENIE_CHARACTER_NAME=ordis
GENIE_ONNX_MODEL_DIR=/home/aiden/voice-satellite/genie_model_reference/genie-ordis/export
GENIE_REFERENCE_AUDIO=/home/aiden/voice-satellite/ordis_voice/ordis_ref.wav
GENIE_REFERENCE_TEXT="What? A parity drift? How is that posible? Have you executed your diagnosic presets?"
GENIE_LANGUAGE=en
```

## Running
To start both the TTS service and the WebSocket server:
```bash
./start_servers.sh
```
Access the UI at `http://0.0.0.0:8001`.
