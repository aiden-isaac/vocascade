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
system:
  role: both                            # both | edge | server  (FR-110)
  transport_auth_mode: trust-network    # or device-identity (Ed25519) — must be explicit
  server_vad_enabled: false
waterfall:
  stages: [stop, converse, high, medium, smalltalk, hermes]
  thresholds: { high: 0.95, medium: 0.65, low: 0.35 }
skills:
  timers:   { enabled: true }
  datetime: { enabled: true }
```

`transport_auth_mode` is **mandatory and validated** (OQ-3): on an unknown value
the server refuses to start rather than defaulting to an open endpoint (FR-111).
For `device-identity`, the key paths live in `.env`
(`EDGE_IDENTITY_KEY_PATH`, optional `AUTHORIZED_KEYS_PATH` allowlist).

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
# winning_stage=high  skill=datetime  confidence=1.0
# trace: stop:0.00  converse:0.00  high:1.00 (WON)

PYTHONPATH=. .venv/bin/python -m pytest tests/test_routing_eval.py -q   # fixtures in CI
```

Agent-class utterances ("what are my tasks today") route to `hermes`, not the
smalltalk floor — the smalltalk gate (FR-033) abstains for them. Those fixtures
are marked `requires_llm` and run against the live local LLM; the deterministic
STOP/HIGH/smalltalk fixtures run headless in CI.

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

## 6. Split topology: roles, latency budget, network failure (US8)

**Role boundary (FR-110).** Hosts come from `.env` (`WS_URL`) — no hardcoded
addresses. Each role's responsibilities:

| Role | Runs |
|------|------|
| **edge** (`python -m vocascade.edge`) | wake word, VAD, audio I/O, pipeline client |
| **server** (`python -m vocascade`) | STT, waterfall, local LLM, TTS, Hermes |

**Transport auth (OQ-3 / FR-111).** Choose deliberately:
- `trust-network` — the network boundary (e.g. Tailscale) is the perimeter; the
  edge connects straight through. Right for co-located or VPN-only deployments.
- `device-identity` — the server presents a nonce, the edge signs it with its
  Ed25519 key, the server verifies it and (if `AUTHORIZED_KEYS_PATH` is set)
  checks the public key against the allowlist. With no allowlist the identity is
  proven but unpinned (TOFU) and the fingerprint is logged. The edge generates
  its key on first run. *(The browser dev UI only works under `trust-network`.)*

**Per-hop latency budget:**

| Hop | Budget |
|-----|--------|
| Wake word + VAD (edge) | imperceptible; segments on-device |
| Device-identity handshake (once per connect) | one extra RTT, before any audio |
| Edge → server audio (WS) | LAN/Tailscale RTT |
| STT (server) | streaming |
| Waterfall: high stage | < 5 ms |
| Waterfall: medium classifier | ~100–200 ms (6 s hard cap, then degrades) |
| Hermes first `message.delta` → TTS | ~300–500 ms |
| STOP → cancellation | < 200 ms |

**Network-failure handling (FR-102).** No hop hangs:

| Failure | Edge behavior |
|---------|---------------|
| Server unreachable at connect | clear status logged, stays in LISTENING (wake word still works) |
| Auth rejected (`device-identity`) | clear status, closes, returns to LISTENING — never streams audio unauthenticated |
| Mid-utterance partition (WS drops) | surfaces "connection lost", returns to LISTENING; the user re-triggers with the wake word |
| Silence (15 s no audio) | disconnects and returns to LISTENING to free the single server session |

In-flight Hermes work on the server is **retained** across an edge
disconnect (FR-061) and spoken proactively when the edge reconnects.

## 7. STOP & barge-in

Say "stop" at any time — while the assistant is speaking, while a skill runs, or
while a Hermes run is in progress — and the activity cancels within ~200 ms,
returning to listening. Speaking over the assistant triggers barge-in (the
in-flight reply stops).
