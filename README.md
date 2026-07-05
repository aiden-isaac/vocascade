# vocascade

A local voice frontend for your **Hermes agent**. Talk to it out loud, and it
talks back in a cloned voice. It runs wake-word detection and transcription on
your own machine, answers chit-chat and simple commands instantly with a small
local model, and hands anything that needs real data or actions to your Hermes
agent — speaking the answer the moment it comes back.

No cloud STT/TTS, no third-party voice framework in the path.

## What you need

- **Python 3.11** and the project virtualenv (`.venv`).
- A reachable **Hermes agent** endpoint (`HERMES_BASE_URL`) — the "heavy brain".
- An OpenAI-compatible **local LLM** endpoint (`LLM_BASE_URL`) — the "fast brain"
  for smalltalk and intent routing. Any local server (LiteLLM, vLLM, Ollama's
  OpenAI shim, …) works.
- A **microphone** (to run the edge client) or just a **browser**.
- *(Optional)* **Genie TTS** for the cloned voice. Without it, vocascade still
  runs and replies as text — voice degrades gracefully, it doesn't break.

## Setup (one-time)

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Optional: set up the voice-cloning TTS in its own venv
bash scripts/setup_genie.sh
```

## Configure

The easy way — a localhost web GUI that writes your config files for you:

```bash
.venv/bin/python -m vocascade.setup_server     # -> http://127.0.0.1:8099
```

Or by hand — copy the templates and set the two endpoints that matter
(`HERMES_BASE_URL` and `LLM_BASE_URL`):

```bash
cp .env.example .env                 # secrets + endpoints
cp config.yaml.example config.yaml   # waterfall, skills, latency
```

Missing or malformed required values fail fast at startup with a located message.

## Run

One command brings up the full local stack — Genie TTS, then the vocascade server
(Ctrl-C stops both):

```bash
bash scripts/run_voice_stack.sh
```

Then connect a client:

```bash
.venv/bin/python -m vocascade.edge   # mic + wake word on this machine
# …or open static/index.html in a browser and talk from there
```

Say the wake word, then your request. (Your Hermes agent and local LLM are
*remote endpoints* you point at — they aren't started by this stack.)

## How it works

Each utterance flows through an ordered **confidence waterfall** — the first
stage to clear its threshold wins:

```
STOP → CONVERSE → HIGH (keywords) → MEDIUM (local-LLM classifier) → SMALLTALK → HERMES
```

- **STOP / CONVERSE** — hard stop/farewell, and multi-turn follow-up capture.
- **HIGH** — fast deterministic keyword skills (datetime, timers).
- **MEDIUM** — a local-LLM intent classifier, its prompt auto-generated from each
  skill's example phrases.
- **SMALLTALK** — the local LLM answers chit-chat in persona, and *abstains* for
  anything needing real data so it falls through to…
- **HERMES** — the always-async agent. It dispatches a run, streams the reply
  into TTS sentence-by-sentence, and delivers late results proactively.

```mermaid
graph TD
    User([User]) <-->|Audio In/Out| Edge[Edge client: wake word, VAD, audio I/O]
    Edge <-->|WebSocket /ws| Server[vocascade server]
    Server --> Waterfall[Confidence waterfall + skills]
    Waterfall -->|local| LocalLLM[Local LLM 'fast brain']
    Waterfall -->|fallback| Hermes[Hermes agent 'heavy brain']
    Server <-->|HTTP| GenieTTS[Genie TTS server]
```

## Add a skill

Drop a file in `user_skills/` with an `@skill` decorator and a `config.yaml`
entry — no changes to the pipeline, STT, or TTS. See `user_skills/alarm.py` for a
worked example.

## More

- Architecture, commands, and gotchas for contributors: [`AGENTS.md`](AGENTS.md).
- Full walkthrough: [`docs/legacy-specs/006-custom-voice-pipeline-waterfall/quickstart.md`](docs/legacy-specs/006-custom-voice-pipeline-waterfall/quickstart.md).
