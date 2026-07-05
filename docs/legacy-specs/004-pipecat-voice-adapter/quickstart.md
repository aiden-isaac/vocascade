# Quickstart: Pipecat Voice Adapter

**Branch**: `pipecat` | **Date**: 2026-06-02

## Prerequisites

- Python 3.11+
- Hermes gateway running (default: `http://localhost:8642/v1`)
- Genie TTS server running with voice character loaded
- LiteLLM running (for online mode — optional for offline testing)
- Honcho running on artemis (for pre-fetch cache — optional)

## Setup

```bash
# Clone and checkout
git clone <repo-url>
git checkout pipecat

# Create virtualenv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings:
#   HERMES_BASE_URL=http://localhost:8642/v1
#   GENIE_TTS_URL=http://localhost:8000
#   GENIE_CHARACTER_NAME=default
#   LITELLM_HEALTH_URL=http://localhost:4000/health
#   HONCHO_API_URL=http://artemis:8001/api
```

## Run

```bash
# Start the voice adapter
python -m voice_adapter

# Or with uvicorn directly
uvicorn voice_adapter.adapter:app --host 0.0.0.0 --port 8000
```

## Connect

Open a browser and navigate to `http://localhost:8000` to use the WebSocket client. The client connects via `ws://localhost:8000/ws` and streams audio to/from the Pipecat pipeline.

## Test

```bash
# Run all tests
PYTHONPATH=. python -m pytest tests/

# Run adapter-specific tests
PYTHONPATH=. python -m pytest tests/unit/test_transcript_manager.py
PYTHONPATH=. python -m pytest tests/unit/test_offline_handler.py
PYTHONPATH=. python -m pytest tests/unit/test_tts_genie.py
```

## Verify Pipeline

1. Start Hermes gateway and Genie TTS
2. Start the adapter: `python -m voice_adapter`
3. Connect via browser
4. Speak a question → expect spoken response within 3 seconds
5. Interrupt mid-response → expect audio stops within 200ms
6. Stop LiteLLM → speak a deferrable command → expect "system is in reduced mode" warning
