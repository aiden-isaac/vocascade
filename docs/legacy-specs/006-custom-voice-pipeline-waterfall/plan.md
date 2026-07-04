# Implementation Plan: Custom Voice Pipeline & Confidence Waterfall

**Branch**: `006-custom-voice-pipeline-waterfall` | **Date**: 2026-06-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/006-custom-voice-pipeline-waterfall/spec.md`

## Summary

Replace the Pipecat framework with a custom asyncio voice pipeline (a ~300-line core loop: audio→wake word→VAD→STT→router→TTS→speaker, barge-in via an interrupt event) and an OVOS-inspired **confidence waterfall** that routes each utterance to the cheapest capable handler, with the Hermes agent as the always-last, always-async stage. Consolidate the two existing packages into one named **`vocascade`** and delete the legacy `voice_satellite` package. Add a declarative skill SDK (auto-discovered bundled + user skills), a latency-masking layer, an explicit session lifecycle with a redesigned end-of-session mechanism, an always-on STOP, multi-turn CONVERSE, per-stage graceful degradation, an explicit edge/server split, a text-only routing eval harness, and a best-effort session-summary sync to the memory service. All framework-agnostic 005 machinery (the `/v1/runs` async client, task broker, delivery coordinator, transcript manager, filler engine) is retained; token-streamed delivery is layered onto the existing run path rather than added as a parallel synchronous path.

> **Sizing note**: "~300 lines" is only the bare `pipeline/pipeline.py` core loop. With the session state machine, STOP propagation, CONVERSE, barge-in, and latency masking, `pipeline/` + `waterfall/` + `session/` together are realistically 1,500–2,500 lines.

## Technical Context

**Language/Version**: Python 3.14 (the project's `.venv`; the default `python` on PATH is miniconda and lacks deps)

**Primary Dependencies**: faster-whisper (STT), silero-vad (VAD), openwakeword (wake word), the Genie TTS HTTP client, httpx (Hermes runs API), PyYAML (config), fastapi/uvicorn/websockets (edge↔server transport). **Removed**: `pipecat-ai[websocket]`.

**Storage**: Local files only — the task journal (`~/.vocascade/tasks.json`, carried from 005), `config.yaml`, and `.env`. No database.

**Testing**: pytest, run as `PYTHONPATH=. .venv/bin/python -m pytest tests/unit -q`; a headless text-only routing eval harness; the existing live Hermes contract test (skips unless a server is reachable).

**Target Platform**: Linux hosts — a server role (currently `jarlaxle`) and an edge/satellite role (currently `athrogate`); must also run on a Raspberry Pi-class edge device (aarch64, CPU-only).

**Project Type**: Single Python package (`vocascade`) providing a server app + an edge client, plus a skill SDK surface.

**Performance Goals**: High-confidence routing imperceptible (sub-5ms); medium-stage classifier ~100–200ms; first spoken word from a prompt-emitting Hermes run within ~300–500ms; STOP effective within ~200ms; local high-confidence turn end-to-end well under 3s.

**Constraints**: CPU-only inference acceptable on the edge; no hardcoded hosts/device indices (config-driven); async-first, no blocking the event loop; streamable end-to-end (no stage buffers a whole payload); graceful degradation on every external call.

**Scale/Scope**: Single-user, single active voice session at a time; a handful to dozens of skills; one-to-few concurrent background Hermes runs. ~51 implementation tasks across 13 phases.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Hardware Agnosticism | ✅ PASS | No hardcoded device indices or hosts; edge/server roles and hosts are config-driven (FR-110). CPU-only inference targeted for the edge; Pi (aarch64) is an explicit target. |
| II. Configuration-Driven Design | ✅ PASS | Structural config in `config.yaml`, secrets/URLs in `.env`, example config maintained, fail-fast on missing/malformed config (FR-130, FR-131). The constitution explicitly permits a central `config.yaml`. |
| III. Modular Architecture | ✅ PASS | Removing Pipecat and the skill SDK both increase modularity; each waterfall stage and skill is independently importable/testable (FR-015, FR-024). Acyclic dependency direction is enforced (see Project Structure). |
| IV. Async-First I/O | ✅ PASS | The pipeline is raw asyncio; barge-in/STOP use a structured interrupt and clean cancellation (FR-002, FR-070, FR-071). CPU-bound STT/VAD/wake-word inference is offloaded off the event loop. Streamed Hermes delivery keeps the path non-buffering (FR-051). |
| V. Documentation Discipline | ✅ PASS | This feature is fully specced before code; the eval harness and quickstart document the routing surface; the skill SDK contract is pinned in `contracts/skill-sdk.md`; README/AGENTS updated in the Polish phase. |
| VI. Resilient Error Handling | ✅ PASS | Per-stage graceful degradation, Hermes-unreachable and server-unreachable handling, best-effort memory sync, and the 005 reconnect-via-snapshot reconciliation (FR-100–FR-102, FR-091, FR-053). |

No violations — Complexity Tracking section omitted.

## Project Structure

### Documentation (this feature)

```text
specs/006-custom-voice-pipeline-waterfall/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 — open decisions resolved before code
├── data-model.md        # Phase 1 — entities
├── quickstart.md        # Phase 1 — run/verify the new stack
├── contracts/
│   ├── skill-sdk.md     # The stable skill SDK surface user skills depend on
│   └── hermes-api.md    # Re-affirms 005's pinned async runs API (incl. message.delta)
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 — /speckit-tasks output
```

### Source Code (repository root)

The current `voice_adapter/` package is renamed to `vocascade/`; the kept `voice_satellite/` library modules are moved in and `voice_satellite/` is deleted (US0, a single mechanical commit).

```text
vocascade/
├── pipeline/
│   ├── pipeline.py        # NEW custom asyncio core loop (~300 lines) + interrupt event
│   ├── vad.py             # PORT of EdgeVADProcessor; + server-side silero per research
│   ├── tts.py             # PORT of tts_genie.py off Pipecat TTSService; slow-first-chunk tolerance
│   └── latency.py         # NEW latency-masking layer (fillers, optimistic openings)
├── transport/
│   ├── serializer.py      # PORT of RawFrameSerializer (WS JSON/binary codec)
│   └── server.py          # NEW edge↔server transport endpoint (replaces retired FastAPI server)
├── waterfall/
│   ├── router.py          # NEW resolve(): ordered stages, config-driven order/thresholds
│   ├── types.py           # ConfidenceResult, WaterfallStage (ABC)
│   ├── classifier.py      # Medium-stage prompt auto-generation from skill examples
│   └── stages/
│       ├── stop.py        # STOP/system (always first)
│       ├── converse.py    # multi-turn claim
│       ├── high.py        # keyword/regex
│       ├── medium.py      # local-LLM classifier
│       └── hermes.py      # always-async run dispatch + message.delta streaming
├── skills/
│   ├── __init__.py        # @skill decorator
│   ├── registry.py        # SkillRegistry + user-skill auto-discovery
│   ├── context.py         # SkillContext, ToolBag
│   └── base_skills/       # smalltalk.py, timers.py, datetime.py, stop.py
├── session/
│   ├── state.py           # SessionState
│   ├── state_machine.py   # passive→active→speaking→passive
│   ├── teardown.py        # PORT of redesigned end-of-session (farewell backstop + sentinel)
│   └── summary.py         # session-end memory-service summary (US10)
├── stt/whisper.py         # MOVED from voice_satellite/stt/whisper_stt.py
├── tts/genie_client.py    # MOVED from voice_satellite/tts/genie_client.py
├── tts/sentence_splitter.py  # MOVED from voice_satellite/tts/sentence_splitter.py
├── audio/effects.py       # MOVED from voice_satellite/audio/effects.py
├── gateway/hermes_client.py  # MOVED from voice_satellite/gateway/hermes_client.py
├── eval/
│   ├── route_harness.py   # text-in → routing-decision-out
│   └── fixtures.jsonl     # labeled routing fixtures
├── delivery.py            # KEEP (005) — proactive delivery coordinator
├── task_broker.py         # KEEP (005) — run lifecycle + result routing
├── hermes_run_client.py   # KEEP (005) — /v1/runs async client
├── transcript_manager.py  # KEEP (005) — sliding-window history + task state
├── filler_engine.py       # KEEP (005) — pre-rendered ack/filler PCM
├── pre_fetch_cache.py     # KEEP (005) — context enrichment cache
└── config.py              # config.yaml + .env loader (extended from voice_adapter/config.py)

user_skills/               # auto-discovered user skills (repo root)
config.yaml / config.yaml.example
tests/{unit,integration,contract}/
```

**Dependency direction (must stay acyclic)**: `pipeline → waterfall → skills → gateway/stt/tts`, with `session/` and `transport/` as leaf utilities. The Hermes machinery (`hermes_run_client.py`, `task_broker.py`, `delivery.py`) sits at the gateway/Hermes layer, below the waterfall's `hermes` stage. Collapsing two packages into one risks circular imports; this ordering is the guard.

**Structure Decision**: Single package `vocascade` (decision from the planning session: reshape the active `voice_adapter/` in place, absorb the `voice_satellite/` library, delete the old package). The name is final (chosen by the user); earlier planning used a `PROJECT_PKG` placeholder that resolves to `vocascade`.
