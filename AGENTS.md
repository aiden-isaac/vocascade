# Voice Satellite — Agent Instructions

## Repo layout

```
voice_satellite/        # Application package
├── server.py           # FastAPI app: WS endpoint, lifespan, gateway factory
├── config.py           # Frozen dataclass SatelliteConfig, dotenv loader
├── __main__.py         # Bootstrap + uvicorn launcher (health report)
├── gateway/            # OpenClawClient | HermesClient (swappable via GATEWAY_BACKEND)
├── stt/                # faster-whisper wrapper (WhisperSTT)
├── tts/                # GenieTTSClient, sentence_splitter
├── audio/              # FillerEngine, DSP effects chain (pitch/tremolo/overdrive/bitcrush/stutter)
└── session/            # ConversationSession + state machine
static/                 # index.html, fillers/, wakeword/
scripts/                # download_wakeword_models.sh, generate_fillers.py, etc.
tests/unit/             # All current tests live here (60 tests)
specs/                  # Speckit specs & plans (001-voice-satellite-core, 002-hermes-gateway)
old_project/            # Legacy code — do not modify
```

Entry point: `python -m voice_satellite` → bootstraps STT/TTS/gateway, prints health report, then runs uvicorn on `server:app`.

## Commands

**Setup:**
```
cp .env.example .env          # populate with your settings
pip install -r requirements.txt   # or activate .venv if present
bash scripts/download_wakeword_models.sh   # downloads ONNX models to static/wakeword/
```

**Run:**
```
uvicorn voice_satellite.server:app --host 0.0.0.0 --port 8000
# or equivalently:
python -m voice_satellite
```

**Tests (from repo root):**
```
PYTHONPATH=. python -m pytest tests/
```
No conftest.py, no pytest.ini — flat discovery under `tests/`. Tests import `voice_satellite` directly, so `PYTHONPATH=.` is required.

**Codegen / helpers:**
```
python scripts/generate_character.py      # generates Genie TTS character profile
python scripts/generate_fillers.py        # renders filler audio clips from text
```

## Gotchas

- **Backend toggle**: `GATEWAY_BACKEND=hermes` (default) vs `openclaw`. The `__main__.py` bootstrap currently only tests the OpenClaw client path; Hermes is wired in `server.py:get_gateway_client()`. Adding Hermes to the bootstrap health report requires an explicit code change.
- **Hermetic failure**: if `OPENCLAW_GATEWAY_TOKEN` is missing AND `GATEWAY_BACKEND=openclaw`, startup exits immediately. If it's missing with `hermes`, startup continues (token is optional for Hermes).
- **TTS degradation**: if any of `GENIE_ONNX_MODEL_DIR`, `GENIE_REFERENCE_AUDIO`, `GENIE_REFERENCE_TEXT` is unset, TTS runs degraded (text-only, no voice cloning). Set `VOICE_SATELLITE_SKIP_GENIE_INIT=true` to skip TTS init entirely.
- **Single WebSocket session**: the `_session_lock` enforces at-most-one active WS connection. Concurrent connections are rejected with close code 1008.
- **Filler audio**: PCM files live in `static/fillers/<category>/` as `.pcm`. Categories and fallback texts are in `static/fillers.json`. Files are served as base64 over WebSocket at 32 kHz sample rate.
- **Wakeword**: models downloaded by `download_wakeword_models.sh` go to `static/wakeword/`. The `eden_wakeword.onnx` at the repo root is also used — check `static/wakeword/model.json` for the active model reference.
- **Character effects**: `get_character_effects_config()` applies randomized glitch effects for `"ordis"` / `"default"`. Other characters get `{}` (no effects). Effects mutate numpy arrays in-place-style; caller should not reuse the input buffer after passing through `apply_effect_chain()`.

## Spec workflow

This repo uses the **Speckit** workflow. Design artifacts live in `specs/<naming>/`:
- `spec.md` — feature specification
- `plan.md` — implementation plan
- `tasks.md` — actionable task breakdown

When modifying existing features, read the corresponding spec/plan first. When creating new features, follow the speckit workflow (spec → plan → tasks → implement).
