# Quickstart: vocascade voice stack

Get the de-Pipecat `vocascade` stack running and verify routing without audio.

> This describes the **target** state after 006. During implementation, the MVP
> (US0 + US1) is the first runnable slice; later sections light up as their user
> stories land.

## 1. Environment

Use the project `.venv` (the default `python` is miniconda and lacks deps):

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/unit -q   # sanity: retained tests green
```

Dependencies: faster-whisper, silero-vad, openwakeword, the Genie TTS client,
httpx, PyYAML, fastapi/uvicorn/websockets. `pipecat-ai` is **not** installed.

## 2. Configure

Two files, clear split (research OQ-4):

- **`.env`** — secrets and endpoints: `HERMES_BASE_URL`, `HERMES_API_KEY`,
  `LLM_BASE_URL`/`LLM_MODEL` (local LLM), `GENIE_TTS_URL`, Honcho URL (optional).
- **`config.yaml`** — structure: waterfall order/thresholds, per-skill settings,
  edge/server roles, transport auth mode. Copy `config.yaml.example` and edit.

```yaml
waterfall:
  order: [stop, converse, high, medium, smalltalk, hermes]
  medium_threshold: 0.5
  smalltalk_confidence: 0.35
skills:
  timers:   { enabled: true }
  datetime: { enabled: true }
transport:
  auth: device_identity        # or: trusted_network  (Tailscale boundary) — must be explicit
roles:
  edge:   [wakeword, vad, audio_io, pipeline_client]
  server: [stt, waterfall, local_llm, tts, hermes]
```

The app fails fast with a located message if a required value is missing or the
YAML is malformed.

## 3. Run the stack

```bash
bash scripts/run_voice_stack.sh        # Genie TTS, then the vocascade server
.venv/bin/python -m vocascade          # server only (Genie already up)
.venv/bin/python -m vocascade.edge     # edge/satellite client (mic + wake word)
```

Topologies:
- **Co-located**: edge + server on one host; `transport.auth: trusted_network` is fine.
- **Split**: edge on `athrogate`, server on `jarlaxle` over Tailscale; choose the
  auth mode deliberately.

## 4. Verify routing without audio (the eval harness)

The fastest way to confirm skills route correctly — no mic, STT, or TTS:

```bash
PYTHONPATH=. .venv/bin/python -m vocascade.eval.route_harness "what time is it"
# → winning_stage=high  skill=datetime  confidence=0.92  trace=[stop:0, converse:0, high:0.92]

PYTHONPATH=. .venv/bin/python -m pytest tests/test_routing_eval.py -q   # fixtures in CI
```

Add a fixture line to `vocascade/eval/fixtures.jsonl` for each new skill and run
the harness in CI; target ≥95% expected-stage accuracy (SC-004).

## 5. Add a skill (no audio code)

Drop a file in `user_skills/` and add a `config.yaml` entry:

```python
# user_skills/weather.py
from vocascade.skills import skill, SkillContext

@skill(name="weather", examples=["what's the weather", "is it going to rain"],
       keywords=["weather", "forecast", "rain"])
async def handle(intent, entities, ctx: SkillContext) -> str:
    return await ctx.tools.weather.brief()
```

Restart; the medium-stage classifier prompt regenerates automatically to include
the new examples. Verify with the harness before touching audio.

## 6. Latency budget (per hop, split topology)

| Hop | Budget |
|-----|--------|
| Wake word + VAD (edge) | imperceptible; segments on-device |
| Edge → server audio (WS) | LAN/Tailscale RTT |
| STT (server) | streaming |
| Waterfall: high stage | < 5 ms |
| Waterfall: medium classifier | ~100–200 ms |
| Hermes first `message.delta` → TTS | ~300–500 ms |
| STOP → cancellation | < 200 ms |

## 7. STOP & barge-in

Say "stop" at any time — while the assistant is speaking, while a skill runs, or
while a Hermes run is in progress — and the activity cancels within ~200 ms,
returning to listening. Speaking over the assistant triggers barge-in (the
in-flight reply stops).
