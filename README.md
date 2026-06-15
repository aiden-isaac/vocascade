# vocascade

A low-latency local voice assistant built around a custom asyncio voice pipeline
and an OVOS-inspired **confidence-waterfall** skill router. It captures mic audio,
runs wake-word + VAD on the edge, transcribes locally, routes each utterance
through a tiered waterfall of skills, and speaks character replies with a
voice-cloning TTS — falling back to the **Hermes** agent (the "heavy brain") for
anything needing real data or external actions.

Everything lives in a single package, **`vocascade/`** (the old `voice_adapter/`
and `voice_satellite/` packages have been consolidated and removed; there is no
third-party voice framework in the path).

## How it works

**Two-brain routing.** Each utterance flows through an ordered waterfall — the
first stage to clear its confidence threshold wins:

```
STOP → CONVERSE → HIGH (keywords) → MEDIUM (local-LLM classifier) → SMALLTALK → HERMES
```

- **STOP / CONVERSE** — hard "stop"/farewell, and multi-turn follow-up capture.
- **HIGH** — fast deterministic keyword skills (datetime, timers).
- **MEDIUM** — a local-LLM intent classifier whose prompt is auto-generated from
  each skill's example phrases.
- **SMALLTALK** — the local LLM answers chit-chat in the configured persona, and
  *abstains* for anything that needs real data so it falls through to…
- **HERMES** — the always-async agent. It dispatches a run, streams the reply
  sentence-by-sentence into TTS, and delivers late results proactively.

**Skill SDK.** Add a skill by dropping a file in `user_skills/` with an `@skill`
decorator and a config entry — no changes to the pipeline, STT, or TTS.

## Architecture

```mermaid
graph TD
    User([User]) <-->|Audio In/Out| Edge[Edge client: wake word, VAD, audio I/O]
    Edge <-->|WebSocket /ws| Server[vocascade server]
    Server --> Waterfall[Confidence waterfall + skills]
    Waterfall -->|local| LocalLLM[Local LLM 'fast brain']
    Waterfall -->|fallback| Hermes[Hermes agent 'heavy brain']
    Server <-->|HTTP| GenieTTS[Genie TTS server]
```

The deployment splits into an **edge** role (wake word, VAD, audio I/O, pipeline
client) and a **server** role (STT, waterfall, local LLM, TTS, Hermes), with an
explicit transport-auth decision (trusted-network or Ed25519 device identity).

## Quickstart

Use the project `.venv` (the default `python` is miniconda and lacks deps):

```bash
# Tests
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q

# Run the whole local stack (Genie TTS, then the server; Ctrl-C stops both)
bash scripts/run_voice_stack.sh

.venv/bin/python -m vocascade        # server only (Genie already up)
.venv/bin/python -m vocascade.edge   # edge/satellite client (mic + wake word)

# Resolve routing for an utterance, no audio (eval harness)
PYTHONPATH=. .venv/bin/python -m vocascade.eval.route_harness "what time is it"
```

See [`specs/006-custom-voice-pipeline-waterfall/quickstart.md`](specs/006-custom-voice-pipeline-waterfall/quickstart.md)
for the full walkthrough.

## Configuration

Two files, clear split:

- **`config.yaml`** — *structure*: waterfall stage order/thresholds, per-skill
  settings (enabled / filler / smalltalk gate), latency masking, edge/server
  role, transport auth mode. Copy `config.yaml.example`.
- **`.env`** — *secrets and endpoints*: `LLM_BASE_URL`/`LLM_MODEL`,
  `HERMES_BASE_URL`, `GENIE_TTS_URL`, key paths, optional `MEMORY_SUMMARY_URL`.
  Copy `.env.example`.

Missing or malformed required values fail fast with a located message.

## Contributing

Changes should follow the project constitution and the active spec set under
`specs/006-custom-voice-pipeline-waterfall/`. Read the relevant spec before
changing a feature; see also [`CLAUDE.md`](CLAUDE.md) for the architecture and
the gotchas that bite.
