# Quickstart: Setup GUI

Run the setup server (standalone — does not start the voice pipeline):

```bash
.venv/bin/python -m vocascade.setup_server      # -> http://127.0.0.1:8099
# override the port with SETUP_PORT=8097
```

Open `http://127.0.0.1:8099` and use the tabs:

- **Service** — host/port, LLM / Hermes URLs + API keys, Whisper model, sample rates. Save writes `.env`.
- **Voice** — pick a Genie profile (auto-fills `GENIE_ONNX_MODEL_DIR`), upload a reference `.wav`, type the transcript. Save writes the `GENIE_*` keys.
- **Fillers** — edit phrases per category; **Save** writes `static/fillers.json`; **Regenerate audio** runs `scripts/generate_fillers.py` (needs the Genie TTS server running).
- **Waterfall** — drag the stages to reorder the routing queue; edit thresholds. Save rewrites only the `stages` block of `config.yaml`, preserving comments.
- **Advanced** — raw `config.yaml` editor; validated (`system`/`waterfall`/`skills` required) before writing.

Then start the stack normally:

```bash
bash scripts/run_voice_stack.sh
```

## Verify

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/unit/test_setup_server.py -q
```

Manual: change `WHISPER_MODEL` on the Service tab → Save → `git diff .env`. Drag a stage on
the Waterfall tab → Save → `git diff config.yaml` shows only the reordered `stages` block,
comments intact, and `python -m vocascade` still loads config cleanly.
