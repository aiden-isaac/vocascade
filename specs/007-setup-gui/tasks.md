# Tasks: Setup GUI

**Input**: Design documents from `/specs/007-setup-gui/`
**Prerequisites**: plan.md (required), spec.md

## Format: `[ID] [P?] [Story] Description`
- **[ID]**: T3## (this feature's block, continuing after 006's T251)
- **[P]**: can run in parallel (different files, no dependency)
- **[Story]**: which user story (US1–US4)

## Phase 1: Setup
- [x] T301 Create `vocascade/setup_server.py` skeleton: standalone FastAPI app, repo-root/path constants, `GET /` serving `static/setup.html`, `__main__` running uvicorn on `127.0.0.1:${SETUP_PORT:-8099}`.
- [x] T302 [P] Create `static/setup.html` shell: dark theme matching `static/index.html`, tab bar (Service/Voice/Fillers/Waterfall/Advanced), toast helper, `api()` fetch helper.

## Phase 2: Foundational
- [x] T303 Helper functions in `setup_server.py`: `require_sections` (mirrors `config.py`), `replace_block_list`, `set_scalar`, `valid_fillers`, `_is_secret`, and the `ENV_GROUPS`/`VOICE_KEYS`/`KNOWN_KEYS` tables sourced from `config.py`.

**Checkpoint**: server boots and serves the page; helpers unit-testable.

## Phase 3: US1 — Service/.env (P1) 🎯 MVP
- [x] T311 [US1] `GET /api/env` (grouped fields with current value/default/secret) and `POST /api/env` (`dotenv.set_key`, restricted to `KNOWN_KEYS`, creates `.env` if absent).
- [x] T312 [US1] Service tab: render grouped fields (password inputs for secrets), Save → `POST /api/env`.

**Checkpoint**: edit a service field, Save, value persisted to `.env`.

## Phase 4: US2 — Voice import (P2)
- [x] T321 [US2] `GET /api/voices` (scan `genie_profiles/*/`) and `POST /api/voice/upload` (raw body, `.wav` + path sanitization).
- [x] T322 [US2] Voice tab: profile dropdown auto-filling `GENIE_*` fields, `.wav` upload, reference-text textarea, Save → `POST /api/env`.

## Phase 5: US3 — Fillers (P2)
- [x] T331 [US3] `GET`/`POST /api/fillers` (shape validation) and `POST /api/fillers/regenerate` (async subprocess → `scripts/generate_fillers.py`).
- [x] T332 [US3] Fillers tab: textarea per category, add-category, Save, Regenerate button with a log box.

## Phase 6: US4 — Waterfall + advanced (P3)
- [x] T341 [US4] `GET`/`POST /api/waterfall` (reorder validation + block rewrite + threshold scalars) and `GET`/`POST /api/config-yaml` (validate then write verbatim).
- [x] T342 [US4] Waterfall tab: native drag-and-drop stage list + threshold inputs; Advanced tab: raw `config.yaml` textarea with inline error surfacing.

## Phase 7: Polish
- [x] T351 [P] `tests/unit/test_setup_server.py`: assert reorder preserves comments, scalar edit keeps comment, `require_sections` rejects missing sections, `valid_fillers` rejects bad shapes.
- [x] T352 [P] One-line pointer to the setup UI in `.env.example`.
- [x] T353 [P] `quickstart.md`.

## Dependencies & Execution Order
- T301/T302 → T303 → US phases. US1–US4 phases are independent of each other once Phase 2 is done (each is its own endpoints + tab).
- **Parallel opportunities**: T302 alongside T301; all Phase 7 tasks in parallel.

## Implementation Strategy
MVP = Phases 1–3 (server + Service/.env tab). US2–US4 layer on independently. All tasks
above are complete; the live smoke test (reorder preserves comments, `load_config()`
still succeeds; validation rejections leave files unchanged) passed.
