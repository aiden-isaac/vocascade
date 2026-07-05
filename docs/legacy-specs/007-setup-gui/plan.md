# Implementation Plan: Setup GUI

**Branch**: `007-setup-gui` | **Date**: 2026-06-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/007-setup-gui/spec.md`

## Summary

A small standalone FastAPI app (`vocascade/setup_server.py`) serving one self-contained
HTML page (`static/setup.html`) that reads/writes the config files a human sets up by
hand: `.env`, `config.yaml`, `static/fillers.json`, and the `genie_profiles/<name>/`
voice dirs. Five tabs: Service (.env), Voice (import + `GENIE_*`), Fillers (edit +
regenerate), Waterfall (drag-reorder stages + thresholds), Advanced (raw `config.yaml`).
Standalone so it works when config is missing/broken and never boots the heavy pipeline.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, uvicorn, python-dotenv, PyYAML — all already present; **no new dependencies**.
**Storage**: flat files in the repo (`.env`, `config.yaml`, `static/fillers.json`, `genie_profiles/`)
**Testing**: pytest (`tests/unit/test_setup_server.py`)
**Target Platform**: localhost web tool (browser UI), bound to `127.0.0.1`
**Project Type**: web service (single FastAPI app) + static HTML
**Performance Goals**: N/A — interactive, file-I/O only; the one slow op (`generate_fillers.py`) runs as an async subprocess
**Constraints**: localhost-only; must not require the voice pipeline; rejected writes must never corrupt a file
**Scale/Scope**: single user, single page, ~10 endpoints

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| Hardware Agnosticism | ✅ PASS | No hardcoded device paths; repo root resolved relative to the module. |
| Configuration-Driven Design | ✅ PASS | The feature *is* a config front-end; env names/defaults sourced from `config.py`; `.env.example` gets a one-line pointer. |
| Modular Architecture | ✅ PASS | One self-contained module + one HTML file; reuses `generate_fillers.py` and the `config.py` validation rule. |
| Async-First I/O | ✅ PASS | Endpoints are trivial file I/O; the filler re-render runs via `asyncio.create_subprocess_exec` (non-blocking). |
| Documentation Discipline | ✅ PASS | This spec set + module docstring + quickstart. |
| Resilient Error Handling | ✅ PASS | Validate-before-write on every mutating endpoint; bad input returns HTTP 400/500 and leaves files byte-identical. |

No violations — no Complexity Tracking needed.

## Project Structure

### Documentation (this feature)
```
specs/007-setup-gui/
├── plan.md           # This file
├── spec.md           # Feature specification
├── tasks.md          # Task breakdown (T301+)
└── quickstart.md     # How to run the setup UI
```
`research.md`, `data-model.md`, `contracts/`, and `issue-map.json` are **not applicable**:
no open design questions, no persisted data model beyond the existing files, no external
contract to pin, and no GitHub issues generated yet.

### Source Code (repository root)
```
vocascade/
└── setup_server.py        # NEW — standalone FastAPI app + endpoints
static/
└── setup.html             # NEW — single-page GUI (vanilla JS, native drag-and-drop)
tests/unit/
└── test_setup_server.py   # NEW — checks the config-rewriting helpers
.env.example               # one-line pointer to the setup UI
```

**Structure Decision**: A standalone server (not routes bolted onto `vocascade/adapter.py`)
because the setup tool must run when config is missing/broken — `adapter.py` fails fast on
a missing `config.yaml` and would also spin up Whisper/Genie/Hermes. The new module reuses
the exact FastAPI + StaticFiles + HTMLResponse pattern from `adapter.py` and the required-
section validation from `config.py`.

## Key design notes
- **`.env` writes** use `dotenv.set_key(..., quote_mode="auto")` — preserves the file and quotes only when needed (verified to round-trip values with apostrophes, e.g. a reference transcript).
- **`config.yaml` comment preservation**: the Advanced tab writes raw text verbatim; the Waterfall tab does a surgical block-list rewrite of `stages:` plus per-key scalar edits of thresholds — no `ruamel.yaml` dependency.
- **Voice upload** reads the raw request body (filename via query param) — avoids adding `python-multipart`.
- **Drag-reorder** uses native HTML5 drag-and-drop — no SortableJS.
