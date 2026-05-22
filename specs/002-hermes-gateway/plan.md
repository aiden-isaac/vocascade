# Implementation Plan: Hermes Gateway Integration

**Branch**: `feat/hermes-gateway` | **Date**: 2026-05-21 | **Spec**: [specs/002-hermes-gateway/spec.md](specs/002-hermes-gateway/spec.md)

**Input**: Feature specification from `specs/002-hermes-gateway/spec.md`

## Summary

Integrate Hermes Agent as the primary AI backend using an OpenAI-compatible HTTP SSE API with session continuity (`X-Hermes-Session-Id`), while preserving the existing OpenClaw WebSocket backend as a swappable configuration option.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: `httpx` for HTTP SSE streaming, FastAPI for local endpoints

**Storage**: N/A

**Testing**: `pytest`

**Target Platform**: Linux server / PC / Raspberry Pi

**Project Type**: Python backend service

**Performance Goals**: Voice latency under 500ms.

**Constraints**: Must maintain existing OpenClaw functionality as a fallback.

**Scale/Scope**: Single client usage.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

No constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/002-hermes-gateway/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── tasks.md
```

### Source Code (repository root)

```text
voice_satellite/
├── gateway/
│   ├── base.py              # GatewayClient base class
│   ├── openclaw_client.py   # Existing openclaw client implementing GatewayClient
│   └── hermes_client.py     # New hermes client implementing GatewayClient
├── server.py                # Updated to use GatewayClient factory
└── config.py                # Updated for new backend toggle
tests/
└── unit/
    ├── test_hermes_client.py
    └── test_config.py
```

**Structure Decision**: Add `hermes_client.py` and `base.py` to `voice_satellite/gateway/`.

## Complexity Tracking

No violations to justify.
