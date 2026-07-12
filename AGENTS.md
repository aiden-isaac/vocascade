# vocascade — Agent Instructions

The single source of truth for working in this repo: layout, commands,
architecture, and the gotchas that bite. (`CLAUDE.md` is a symlink to this file
so Claude Code auto-loads it.) End-user setup lives in [`README.md`](README.md).

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
├── tts/                   # TTSBackend protocol + registry (piper default, genie voice cloning), chunker
├── transport/             # WS serializer + transport-auth gate (trust-network | device-identity)
├── edge/                  # __main__: edge/satellite client (wake word, VAD, audio I/O, WS client)
├── eval/                  # route_harness + fixtures.jsonl (headless routing eval)
├── audio/                 # DSP character effects chain
└── delivery.py, task_broker.py, hermes_run_client.py, filler_engine.py, telemetry.py
user_skills/               # User-provided skills, auto-discovered at startup
static/                    # index.html (browser client), fillers/, wakeword/
scripts/                   # run_voice_stack.sh, generate_fillers.py, etc.
tests/                     # unit/, integration/, contract/
pyproject.toml             # packaging: deps, [edge] extra, console scripts
Dockerfile, docker-compose.yaml   # flagship host install (no audio hardware in the container)
deploy/                    # vocascade-edge.service (systemd user unit)
docs/protocol.md           # edge<->host WS contract (public, versioned)
```

Entry points: `vocascade` (server), `vocascade-edge` (edge client) — console
scripts from `pyproject.toml`; the `python -m vocascade[...edge]` forms still
work, plus `python -m vocascade.setup_server` (localhost config GUI, :8099).

## Architecture (the moving parts)

Edge client (mic, wake word, VAD) or browser ↔ server over one WebSocket `/ws`.
Each utterance flows through an ordered **confidence waterfall** — the first
stage to clear its threshold wins:

```
STOP → CONVERSE → HIGH (keyword skills) → MEDIUM (local-LLM classifier) → SMALLTALK → HERMES
```

**Two-brain dispatch.** The local LLM ("fast brain") answers smalltalk and runs
the medium classifier directly. Anything needing real data or external actions
falls through to **HERMES** ("heavy brain"), which **dispatches asynchronously**
(`task_broker.py`) and returns immediately with a short spoken handoff; the real
result arrives seconds-to-minutes later and is spoken **proactively** via
`delivery.py` (an idle-gated FIFO — nothing speaks while the user/bot is talking).
Tasks outlive voice sessions. `hermes_run_client.py` is the only thing that
speaks HTTP to Hermes (async runs API, reconcile-on-reconnect).

## Commands

Always use the project `.venv` (the default `python` is miniconda and lacks deps).
The package is installed editable (`pip install -e ".[edge]"` from `pyproject.toml`)
— no `PYTHONPATH` tricks, and `requirements.txt` is gone.

```bash
cp .env.example .env && cp config.yaml.example config.yaml   # then edit
.venv/bin/pip install -e ".[edge]"                           # once per venv

.venv/bin/python -m pytest tests/ -q                         # all tests
bash scripts/run_voice_stack.sh                              # Genie TTS + server (genie backend only)
.venv/bin/vocascade                                          # server only (= python -m vocascade)
.venv/bin/vocascade-edge                                     # edge client (= python -m vocascade.edge)
.venv/bin/python -m vocascade.eval.route_harness "what time is it"
.venv/bin/python scripts/generate_fillers.py                 # re-render acknowledge audio
docker compose up -d                                         # host server in a container
```

No `conftest.py`/`pytest.ini`; flat discovery under `tests/`. CI runs
`tests/unit` only, against the base deps (no `[edge]` extra).

## Gotchas

- **Config split (OQ-4)**: `config.yaml` = structure (waterfall order/thresholds,
  per-skill settings, latency, role, transport auth); `.env` = secrets/endpoints.
  Missing required values fail fast with a located message.
- **Two brains, one required**: the LLM (`LLM_BASE_URL` + `LLM_MODEL`, BYOK —
  any OpenAI-compatible endpoint, no defaults, fail-fast) handles smalltalk +
  the medium classifier + the smalltalk gate; Hermes (`HERMES_BASE_URL`) is the
  always-async fallback and is OPTIONAL — empty means local-only mode (hermes
  stage dropped, waterfall exhaustion speaks a can't-help notice). The local
  LLM is never given tool schemas.
- **LLM failure UX**: `LocalLLM` classifies failures (`LLMAuthError` /
  `LLMUnreachableError`); the first classified failure per session is spoken
  specifically ("I can't reach my language model…"), later ones use the generic
  fallback. Startup health report probes both endpoints (3s timeout, never
  blocks startup).
- **Smalltalk gate (FR-033)**: smalltalk abstains for data/action utterances so
  they fall through to Hermes. Toggle with `skills.smalltalk.gate`.
- **Transport auth (OQ-3)**: `transport_auth_mode` MUST be explicit
  (`trust-network` | `device-identity`); the server refuses to start otherwise.
- **Pluggable TTS**: `TTS_BACKEND` picks the voice from a plain registry
  (`vocascade/tts/protocol.py`): `piper` (default — in-process CPU voice,
  stock voice auto-downloads to `TTS_MODELS_DIR`, `TTS_VOICE=female|male`)
  or `genie` (custom voice cloning). Unknown names fail startup fast.
  Pre-`TTS_BACKEND` Genie installs must set `TTS_BACKEND=genie`.
- **TTS degradation**: if the selected backend can't load its voice (genie:
  any of `GENIE_ONNX_MODEL_DIR` / `GENIE_REFERENCE_AUDIO` / `GENIE_REFERENCE_TEXT`
  unset; piper: voice missing and not downloadable), TTS runs text-only.
  `VOICE_SATELLITE_SKIP_GENIE_INIT=true` skips TTS warmup/init entirely.
- **Single WS session**: a module-level `_session_lock` allows one active `/ws`
  connection; concurrent connects are rejected with close code 1008. Documented
  beta limitation; multi-session is the v1.1 headline.
- **Wire protocol is public**: `docs/protocol.md` is the versioned contract for
  all clients. The first frame on every accepted connection is
  `{"type": "hello", "protocol_version": N}` (`vocascade.transport.PROTOCOL_VERSION`
  — single source for server + edge). Any breaking wire change bumps it and
  updates the doc. Downstream audio is JSON base64, NOT binary — the edge
  client decodes `{"type": "audio"}` frames.
- **Wake word default**: `WAKE_WORD_MODEL=hey_jarvis` resolves against the
  models bundled inside the openwakeword package (no download); an existing
  file path is used as-is (custom models). Unresolvable → edge exits with an
  actionable error, it does not silently disable wake word. openwakeword is
  pinned `<0.6` (0.6 renamed the Model kwarg and unbundled the models).
- **Ports**: server `8005` (current `.env`), Genie TTS `127.0.0.1:8000`. A stale
  process on `:8005` makes the stack appear to "shut down on startup" (`ss -ltnp | grep :8005`).
- **Docker builds**: `.dockerignore` is an allowlist — new runtime assets
  outside `vocascade/`/`static/`/`user_skills/` must be added there or the
  image won't contain them.

## Spec workflow

This repo uses the **OpenSpec** workflow; active changes live in `openspec/`
(propose → apply → archive). Read the relevant spec before changing a feature.

Historical Spec-Kit design docs are archived read-only under
`docs/legacy-specs/<NNN-name>/` (`spec.md` → `plan.md` → `tasks.md`). The most
recent is [`006-custom-voice-pipeline-waterfall`](docs/legacy-specs/006-custom-voice-pipeline-waterfall/plan.md)
— the custom asyncio `VoicePipeline` + OVOS-style confidence waterfall, consolidated
into the single `vocascade` package. Supersedes 001–004; extends 005 (Hermes runs API).

## graphify

This project has a local knowledge graph at `graphify-out/` (git-ignored; run
`graphify` to generate). When present, prefer it over raw grep for codebase
questions:

- `graphify query "<question>"` returns a scoped subgraph; `graphify path "<A>" "<B>"`
  for relationships; `graphify explain "<concept>"` for one concept.
- `graphify-out/wiki/index.md` for broad navigation; `graphify-out/GRAPH_REPORT.md`
  only for whole-architecture review.
- After modifying code, run `graphify update .` (AST-only, no API cost).
