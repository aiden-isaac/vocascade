"""
genie/server.py -- Genie TTS server entry point.

Starts the GPT-SoVITS TTS HTTP server on port 8000.
Run from within the genie_model_reference virtual environment:

    source .venv/bin/activate
    python server.py

Or via start_servers.sh which handles venv activation automatically.
"""

import genie_tts as genie

genie.start_server(host="0.0.0.0", port=8000, workers=1)
