# vocascade — Agent Instructions

> For the full architecture, runtime topology, and the gotchas that bite, read
> [`CLAUDE.md`](CLAUDE.md). This file is the quick map.

## Repo layout

```
vocascade/                 # The single application package (no third-party voice framework)
├── adapter.py             # FastAPI app: /ws endpoint, lifespan, app-level wiring
├── __main__.py            # Bootstrap + uvicorn launcher (health report)
├── config.py              # Frozen AdapterConfig; loads config.yaml (structure) + .env (secrets)
├── pipeline/              # Custom asyncio VoicePipeline + stages (vad, stt, router, tts, latency)
├── waterfall/             # Confidence router + stages (stop, converse, high, medium, hermes) + classifier
├── skills/                # @skill registry, SkillContext, base_skills/ (datetime, timers, smalltalk, hermes, stop)
├── session/              # state, state_machine, teardown, summary (session-end memory gist)
├── gateway/               # local_llm (fast brain), auth (Ed25519), hermes_client (chat fallback)
├── stt/                   # faster-whisper wrapper (WhisperSTT)
├── tts/                   # GenieTTSClient (voice cloning)
├── transport/             # WS serializer + transport-auth gate (trust-network | device-identity)
├── edge/                  # __main__: edge/satellite client (wake word, VAD, audio I/O, WS client)
├── eval/                  # route_harness + fixtures.jsonl (headless routing eval)
├── audio/                 # DSP character effects chain
└── delivery.py, task_broker.py, hermes_run_client.py, filler_engine.py, telemetry.py
user_skills/               # User-provided skills, auto-discovered at startup
static/                    # index.html (browser client), fillers/, wakeword/
scripts/                   # run_voice_stack.sh, generate_fillers.py, etc.
tests/                     # unit/, integration/, contract/
old_project/               # Legacy code — do not modify
```

Entry points: `python -m vocascade` (server) and `python -m vocascade.edge` (edge client).

## Commands

Always use the project `.venv` (the default `python` is miniconda and lacks deps).

```bash
cp .env.example .env && cp config.yaml.example config.yaml   # then edit

PYTHONPATH=. .venv/bin/python -m pytest tests/ -q            # all tests
bash scripts/run_voice_stack.sh                              # Genie TTS + server
.venv/bin/python -m vocascade                               # server only
.venv/bin/python -m vocascade.edge                          # edge client (mic + wake word)
PYTHONPATH=. .venv/bin/python -m vocascade.eval.route_harness "what time is it"
.venv/bin/python scripts/generate_fillers.py                # re-render acknowledge audio
```

`PYTHONPATH=.` is required — tests/app import `vocascade` as a top-level package.
No `conftest.py`/`pytest.ini`; flat discovery under `tests/`.

## Gotchas

- **Config split (OQ-4)**: `config.yaml` = structure (waterfall order/thresholds,
  per-skill settings, latency, role, transport auth); `.env` = secrets/endpoints.
  Missing required values fail fast with a located message.
- **Two brains**: the local LLM (`LLM_BASE_URL`) handles smalltalk + the medium
  classifier + the smalltalk gate; Hermes (`HERMES_BASE_URL`) is the always-async
  fallback. The local LLM is never given tool schemas.
- **Smalltalk gate (FR-033)**: smalltalk abstains for data/action utterances so
  they fall through to Hermes. Toggle with `skills.smalltalk.gate`.
- **Transport auth (OQ-3)**: `transport_auth_mode` MUST be explicit
  (`trust-network` | `device-identity`); the server refuses to start otherwise.
- **TTS degradation**: if any of `GENIE_ONNX_MODEL_DIR` / `GENIE_REFERENCE_AUDIO`
  / `GENIE_REFERENCE_TEXT` is unset, TTS runs text-only. `VOICE_SATELLITE_SKIP_GENIE_INIT=true`
  skips TTS init.
- **Single WS session**: a module-level `_session_lock` allows one active `/ws`
  connection; concurrent connects are rejected with close code 1008.
- **Ports**: server `8005` (current `.env`), Genie TTS `127.0.0.1:8000`. A stale
  process on `:8005` makes the stack appear to "shut down on startup" (`ss -ltnp | grep :8005`).

## Spec workflow

This repo uses the **Speckit** workflow; design artifacts live in `specs/<NNN-name>/`
(`spec.md` → `plan.md` → `tasks.md`). Read the relevant spec before changing a feature.

<!-- SPECKIT START -->
**Active feature**: [`006-custom-voice-pipeline-waterfall`](specs/006-custom-voice-pipeline-waterfall/plan.md)
— the custom asyncio `VoicePipeline` + OVOS-style confidence waterfall, consolidated
into the single `vocascade` package. Supersedes 001–004; extends 005 (Hermes runs API).
<!-- SPECKIT END -->
