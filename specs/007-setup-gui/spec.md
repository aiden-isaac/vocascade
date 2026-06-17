# Feature Specification: Setup GUI

**Feature Branch**: `007-setup-gui`
**Created**: 2026-06-17
**Status**: Draft
**Input**: User description: "make some simple GUI that can help setup configs, import voice files, set filler texts, etc."

## Context

Vocascade is configured entirely through hand-edited files: `.env` (service URLs/keys,
Whisper, audio rates, `GENIE_*` voice vars), the committed `config.yaml` (waterfall
stage order, skills, latency), `static/fillers.json` (filler phrases, re-rendered by
`scripts/generate_fillers.py`), and the `genie_profiles/<name>/` voice directories.
First-time setup means knowing which of ~40 env vars matter and editing several files
by hand. This feature adds a small localhost web GUI to do all of that from a browser,
including drag-to-reorder of the waterfall stage queue.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Edit service/network config (Priority: P1)

A user runs the setup server, opens the page, edits the common service settings (host,
port, LLM / Hermes / Genie URLs and API keys, Whisper model, sample rates) on a form,
and saves. The values are written to `.env`, preserving the rest of the file.

**Why this priority**: This is the core ask and the minimum viable tool — getting the
service wired up is what blocks a first voice reply.

**Independent Test**: Launch `python -m vocascade.setup_server`, change `WHISPER_MODEL`,
Save, and confirm `.env` shows the new value with all other lines/comments intact.

**Acceptance Scenarios**:
1. **Given** the setup page is open, **When** the user edits a field and clicks Save, **Then** the key is persisted to `.env` and other keys are untouched.
2. **Given** no `.env` exists yet, **When** the user saves, **Then** `.env` is created with the entered keys.
3. **Given** an API-key field, **When** the page renders it, **Then** the value is masked (password input).

### User Story 2 — Import a voice and set its reference (Priority: P2)

A user picks a Genie voice profile from a dropdown (scanned from `genie_profiles/*/`),
optionally uploads a reference `.wav`, types the reference transcript, and saves. The
`GENIE_*` env vars are set accordingly.

**Why this priority**: Voice cloning is what makes the stack speak in-character; without
it TTS runs in degraded text-only mode.

**Independent Test**: Select `ganyu-v2`, confirm `GENIE_ONNX_MODEL_DIR` auto-fills to its
`export/` dir; upload a `.wav` and confirm it lands in `genie_profiles/ganyu-v2/` and
`GENIE_REFERENCE_AUDIO` points at it; Save and confirm the `GENIE_*` keys in `.env`.

**Acceptance Scenarios**:
1. **Given** profiles exist on disk, **When** the Voice tab loads, **Then** each profile is listed with whether an `export/` dir is present.
2. **Given** a profile is selected, **When** the user uploads a non-`.wav`, **Then** the upload is rejected.
3. **Given** valid `GENIE_*` fields, **When** the user saves, **Then** the keys are written to `.env`.

### User Story 3 — Edit and regenerate filler phrases (Priority: P2)

A user edits the filler phrases per category in textareas, saves them to
`static/fillers.json`, and clicks a button to re-render the pre-rendered PCM clips.

**Independent Test**: Edit an `acknowledge` phrase, Save, confirm `static/fillers.json`
updated; click Regenerate (with Genie up) and confirm the script output appears and
`static/fillers/acknowledge/*.pcm` is refreshed.

**Acceptance Scenarios**:
1. **Given** the Fillers tab, **When** the user saves a malformed shape (not category→list of strings), **Then** the save is rejected and the file is unchanged.
2. **Given** Genie is running, **When** the user clicks Regenerate, **Then** `scripts/generate_fillers.py` runs and its stdout is shown.

### User Story 4 — Reorder the waterfall and edit advanced config (Priority: P3)

A user drags the waterfall stages into a new order and edits the routing thresholds on a
dedicated tab; or edits the raw `config.yaml` in an advanced textarea. Saves are
validated before writing and comments are preserved.

**Independent Test**: Drag `hermes` above `converse`, Save, and confirm `git diff
config.yaml` shows only the `stages` block reordered with all comments intact and
`load_config()` still succeeds.

**Acceptance Scenarios**:
1. **Given** a reordered stage list, **When** the user saves, **Then** only the `stages` block changes; comments and the rest of the file are preserved.
2. **Given** a stage list that adds/removes a stage (not a pure reorder), **When** the user saves, **Then** the save is rejected.
3. **Given** the advanced tab, **When** the user saves YAML missing a required section (`system`/`waterfall`/`skills`), **Then** the save is rejected with the offending section named and the file is unchanged.

### Edge Cases
- `config.yaml` missing or malformed → the server still serves the page; saves that would produce invalid config are rejected; the file is never left half-written.
- `.env` absent → created on first save.
- Genie offline → Regenerate returns the script's error output (HTTP 500) without crashing the server.
- Uploaded filename containing path separators → stripped to a basename; writes stay inside `genie_profiles/<name>/`.

## Requirements *(mandatory)*

### Functional Requirements
- **FR-001**: System MUST serve a single-page setup GUI over HTTP, bound to `127.0.0.1` only.
- **FR-002**: System MUST run standalone without booting the voice pipeline, so it works when config is missing or broken.
- **FR-003**: System MUST read and write the common `.env` keys, persisting via an in-place writer that preserves unrelated lines (`dotenv.set_key`).
- **FR-004**: System MUST mask secret fields (API keys) in the UI.
- **FR-005**: System MUST list Genie voice profiles found under `genie_profiles/*/` and accept a `.wav` upload into a selected profile, rejecting non-`.wav` files and sanitizing the destination path.
- **FR-006**: System MUST read/write `static/fillers.json`, validating the shape (object of category → list of strings) before writing.
- **FR-007**: System MUST trigger `scripts/generate_fillers.py` on request and return its output.
- **FR-008**: System MUST let the user reorder the waterfall `stages` via drag-and-drop and persist the new order by surgically rewriting only that block of `config.yaml`, preserving comments; it MUST reject changes that are not a pure reordering.
- **FR-009**: System MUST let the user edit waterfall thresholds and the raw `config.yaml`, validating (`yaml.safe_load` + required sections `system`/`waterfall`/`skills`) before writing; invalid input MUST leave the file unchanged.

### Key Entities
- **Env field**: a known `.env` key with group, current value, default, and a secret flag — names/defaults mirror `vocascade/config.py`.
- **Voice profile**: a `genie_profiles/<name>/` directory with an optional `export/` ONNX dir and reference `.wav` files.
- **Filler set**: `static/fillers.json`, an object of category → list of phrase strings.

## Success Criteria *(mandatory)*

### Measurable Outcomes
- **SC-001**: A fresh clone can be configured to a first voice reply without hand-editing any config file.
- **SC-002**: A waterfall reorder is reflected in `config.yaml` with all comments preserved and `load_config()` still succeeding.
- **SC-003**: No invalid save ever corrupts `config.yaml`, `.env`, or `fillers.json` (rejected writes leave files byte-identical).
- **SC-004**: The feature adds zero new third-party dependencies.

## Assumptions
- The ONNX voice model is produced by Genie's own training/export step separately; the GUI only points at an existing `export/` directory, it does not train voices.
- The tool is single-user and localhost-trusted; no authentication is provided (and it must not be exposed off-localhost).
- Single-file uploads are sufficient (raw request body), so no multipart form handling is needed.
