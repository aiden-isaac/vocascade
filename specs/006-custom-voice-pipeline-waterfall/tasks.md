---
description: "Task list for 006-custom-voice-pipeline-waterfall"
---

# Tasks: Custom Voice Pipeline & Confidence Waterfall

**Input**: Design documents from `/specs/006-custom-voice-pipeline-waterfall/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Test tasks ARE included — routing correctness, session/STOP cancellation, and the Hermes stage are central to this feature (see spec Assumptions).

**Organization**: Grouped by user story (US0–US10) for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story the task serves (US0–US10). Setup/Foundational/Polish carry no story label.
- Task IDs start at **T201** to avoid collision with 004 (T001–T062) and 005 (T101–T141).
- Paths use the target single package `vocascade/`.

---

## Phase 0: Contract pinning & decision gate (BLOCKING)

**Purpose**: Resolve the three research gates and pin the SDK + Hermes contracts before any code depends on them.

- [x] T201 [US0] Re-affirm the Hermes contract in `specs/006-custom-voice-pipeline-waterfall/contracts/hermes-api.md` (unchanged from 005) and extend `tests/contract/test_hermes_api.py` to assert `GET /v1/runs/{id}/events` emits ≥1 `message.delta` with non-empty `delta` before `run.completed` (research OQ-1). Record fallback if absent.
- [x] T202 [P] [US2] Pin the skill SDK surface in `specs/006-custom-voice-pipeline-waterfall/contracts/skill-sdk.md` (decorator signature, `SkillContext` fields, `ConfidenceResult`, stage ABC) — freeze before user-skill code depends on it.
- [x] T203 [P] [US0] Resolve and record `research.md` decisions: authoritative VAD location (OQ-2), transport auth mode (OQ-3), `config.yaml`/`.env` boundary (OQ-4), classifier prompt-gen + clamping (OQ-5).

**Checkpoint**: No open ⚠️ in contracts; OQ-1 verified live (or fallback recorded).

---

## Phase 1: Setup (single package, deps, config)

**Purpose**: Collapse to one package and stand up config; this is the structural foundation for everything.

- [x] T204 [US0] **Mechanical repackage (pure move — no logic/Pipecat changes, tests green before & after, independently revertable)**: rename `voice_adapter/` → `vocascade/`; move `voice_satellite/{stt/whisper_stt.py→stt/whisper.py, tts/genie_client.py, tts/sentence_splitter.py, audio/effects.py, gateway/hermes_client.py}` into `vocascade/`; fix every import across the repo and `tests/`; delete `voice_satellite/`. Nothing else in this commit.
- [x] T205 [US0] Add `silero-vad` and `PyYAML`; remove `pipecat-ai[websocket]` — `requirements.txt` (and `pyproject.toml` if present).
- [x] T206 [P] [US0] Add a `config.yaml` loader + `config.yaml.example` (structure vs. `.env` secrets per OQ-4; fail-fast on missing/malformed) — `vocascade/config.py`.
- [x] T207 [P] [US0] Delete throwaway/Pipecat-coupled tests: `test_pipecat.py`, `test_pipeline.py`, `test_vad.py`, `test_ws.py`, `tests/unit/test_adapter.py`, `tests/unit/test_tts_genie.py`.

**Checkpoint**: `grep -ri "voice_satellite" .` = 0, `voice_satellite/` gone, repo imports/tests green on the new package name.

---

## Phase 2: Foundational (BLOCKING)

**Purpose**: Port the Pipecat-coupled pieces to framework-free equivalents and lay the pipeline/router skeletons. No user story can complete until this is done.

- [x] T208 [US0] Port `RawFrameSerializer` (WS JSON/binary codec) off Pipecat frames into a standalone codec — `vocascade/transport/serializer.py`.
- [x] T209 [US0] Port the VAD shim into a framework-free stage; add optional server-side `silero-vad` per OQ-2 (edge VAD remains authoritative) — `vocascade/pipeline/vad.py`.
- [x] T210 [US0] Reshape `GenieTTSService` off the Pipecat `TTSService` base into a plain async TTS sink wrapping `vocascade/tts/genie_client.py`; **preserve slow-first-chunk tolerance** (4–7s, FR-005) — `vocascade/pipeline/tts.py`.
- [x] T211 [US1] `VoicePipeline` skeleton — the ~300-line asyncio core loop + the interrupt `asyncio.Event` shared by all stages — `vocascade/pipeline/pipeline.py`.
- [x] T212 [P] [US2] Define `ConfidenceResult`, `WaterfallStage` ABC, and `SessionState` — `vocascade/waterfall/types.py`, `vocascade/session/state.py`.

**Checkpoint**: package imports with no `pipecat`/`voice_satellite`; pipeline skeleton instantiable; types available.

---

## Phase 3: User Story 1 — Speak end-to-end through the custom pipeline (Priority: P1) 🎯 MVP

**Goal**: Wake word → VAD → STT → router → TTS → speaker for one local turn, no framework.

**Independent Test**: Say the wake word, ask a smalltalk question, hear a persona reply; no `pipecat` import in the path.

- [x] T213 [US1] Wire the loop: mic/transport in → wake word → VAD → STT (`vocascade/stt/whisper.py`) → router → ported TTS → speaker — `vocascade/pipeline/pipeline.py` (transport wiring in `vocascade/adapter.py`).
- [x] T214 [US1] Minimal 2-stage waterfall (SMALLTALK floor + HERMES passthrough) so a turn always resolves — `vocascade/waterfall/router.py`.
- [x] T215 [US1] Smalltalk skill (local-LLM persona, fixed 0.35 floor) — `vocascade/skills/base_skills/smalltalk.py`.
- [x] T216 [P] [US1] Integration test of the round-trip with mocked audio I/O — `tests/integration/test_pipeline_roundtrip.py` (plus server-level `tests/integration/test_server_ws.py`).

**Checkpoint**: 🎯 **STOP & VALIDATE** — voice turn works end-to-end, Pipecat removed.

---

## Phase 4: User Story 2 — Confidence waterfall & skill SDK (Priority: P1)

**Goal**: Full ordered routing + the declarative skill SDK.

**Independent Test**: The eval harness resolves fixtures to the expected stage/skill.

- [x] T217 [US2] `@skill` decorator + `SkillRegistry` (registration, duplicate-name guard) — `vocascade/skills/__init__.py`, `vocascade/skills/registry.py`.
- [x] T218 [US2] `SkillContext` + `ToolBag` — `vocascade/skills/context.py`.
- [ ] T219 [US2] HIGH stage (keyword/regex, >0.8, <5ms) — `vocascade/waterfall/stages/high.py`.
- [ ] T220 [US2] MEDIUM stage classifier + startup prompt auto-generation from skill `examples` (capped, clamped per OQ-5) — `vocascade/waterfall/stages/medium.py`, `vocascade/waterfall/classifier.py`.
- [ ] T221 [US2] Config-driven stage order/thresholds in the router — `vocascade/waterfall/router.py`, `config.yaml`.
- [ ] T222 [P] [US2] Unit tests: stage ordering, threshold/tie resolution, prompt regeneration — `tests/unit/test_waterfall.py`, `tests/unit/test_skill_registry.py`.

**Checkpoint**: utterances route deterministically; adding a skill changes the classifier prompt.

---

## Phase 5: User Story 3 — Hermes always-async last stage (Priority: P1)

**Goal**: Every Hermes query dispatches via `/v1/runs`; `message.delta` streams into TTS; late results delivered proactively. One path, no heuristic.

**Independent Test**: prompt-emitting run streams to TTS; slow run delivered proactively when complete.

- [ ] T223 [US3] HERMES stage: dispatch every query via `/v1/runs` using KEEP `vocascade/hermes_run_client.py` + `vocascade/gateway/hermes_client.py` — `vocascade/waterfall/stages/hermes.py`.
- [ ] T224 [US3] Stream `message.delta` events into TTS as they arrive; KEEP `vocascade/task_broker.py` + `vocascade/delivery.py` handle late/terminal completion (proactive delivery). No stream-vs-run branch — `vocascade/waterfall/stages/hermes.py`.
- [ ] T225 [P] [US3] Tests: streamed delta→TTS path; late-completion proactive delivery; in-flight task retained at session end; stream-drop reconcile-via-snapshot — `tests/unit/test_hermes_stage.py`.

**Checkpoint**: heavy-brain requests work both fast (streamed) and slow (proactive), same machinery.

---

## Phase 6: User Story 4 — Latency masking (Priority: P2)

**Goal**: No dead air — per-stage fillers + streamed continuation.

**Independent Test**: HIGH no filler; MEDIUM tool-filler then stream; HERMES query-filler then streamed delta.

- [ ] T226 [US4] Filler policy layer (HIGH none / MEDIUM tool-specific / HERMES query-specific) using KEEP `vocascade/filler_engine.py` — `vocascade/pipeline/latency.py`.
- [ ] T227 [US4] Optimistic partial openings (local-LLM) + voice-optimized short responses — `vocascade/pipeline/latency.py`.
- [ ] T228 [P] [US4] Latency tests/measurements (SC-002/SC-003) — `tests/unit/test_latency.py`.

**Checkpoint**: routing outcomes feel instant.

---

## Phase 7: User Story 5 — Session lifecycle, STOP, CONVERSE (Priority: P2)

**Goal**: Clean session states, redesigned ENDSESSION, always-on STOP, multi-turn claims.

**Independent Test**: drive passive→active→speaking→passive; "stop" cancels mid-TTS/mid-skill/mid-Hermes; a multi-turn skill claims the next utterance.

- [ ] T229 [US5] Session state machine (passive→active→speaking→passive; in-flight tasks retained on teardown) — `vocascade/session/state_machine.py`.
- [ ] T230 [US5] Redesigned ENDSESSION: port the farewell-phrase backstop + model sentinel out of the old `TeardownInterceptor` into the new pipeline; disarm on re-engage — `vocascade/session/teardown.py`.
- [ ] T231 [US5] STOP/SYSTEM stage + cancellation propagation through TTS sink, active skill, and Hermes run/stream via the interrupt Event — `vocascade/waterfall/stages/stop.py`, `vocascade/pipeline/pipeline.py`.
- [ ] T232 [US5] CONVERSE stage + multi-turn claim (claim/resume/timeout/STOP-release) — `vocascade/waterfall/stages/converse.py`.
- [ ] T233 [P] [US5] Tests: stop mid-TTS/mid-skill/mid-Hermes; converse claim + timeout release; farewell vs silence-timeout idempotent teardown — `tests/unit/test_stop.py`, `tests/unit/test_converse.py`, `tests/unit/test_session.py`.

**Checkpoint**: control flow is reliable and conversational.

---

## Phase 8: User Story 6 — Bundled skills, user skills, per-skill config (Priority: P2)

**Goal**: Useful built-ins + zero-audio-edit extensibility.

**Independent Test**: drop a skill in `user_skills/` + config entry → auto-discovered and routable; disabled skill not registered; import error isolated.

- [ ] T234 [P] [US6] Timers skill — `vocascade/skills/base_skills/timers.py`.
- [ ] T235 [P] [US6] Datetime skill — `vocascade/skills/base_skills/datetime.py`.
- [ ] T236 [P] [US6] Stop skill handler (verbal/explicit) — `vocascade/skills/base_skills/stop.py`.
- [ ] T237 [US6] `user_skills/` auto-discovery + per-skill config + import-failure isolation — `vocascade/skills/registry.py`, `config.yaml`, `user_skills/.gitkeep`.
- [ ] T238 [P] [US6] Tests: discovery, disabled-skill exclusion, import-failure isolation — `tests/unit/test_user_skills.py`.

**Checkpoint**: contributors can add skills without touching audio code (SC-005).

---

## Phase 9: User Story 7 — Graceful degradation (Priority: P2)

**Goal**: No silent failures at any stage.

**Independent Test**: force tool-call failure / Hermes unreachable / server down → defined spoken-or-surfaced fallback.

- [ ] T239 [US7] Per-stage fallback: tool-call failure degrades or speaks a graceful error; Hermes-unreachable notice with local loop continuing; server-unreachable status on the edge — `vocascade/waterfall/router.py`, `vocascade/pipeline/pipeline.py`.
- [ ] T240 [P] [US7] Tests: each failure mode degrades, none silent — `tests/unit/test_degradation.py`.

**Checkpoint**: resilient on unreliable networks (Constitution VI).

---

## Phase 10: User Story 8 — Satellite/server split (Priority: P2)

**Goal**: Explicit, config-driven edge/server boundary with documented budgets and an explicit transport-auth decision.

**Independent Test**: configure roles via config (no hardcoded hosts); edge runs wakeword+VAD+IO+client, server runs STT+waterfall+LLM+TTS+Hermes; partition handled per policy.

- [ ] T241 [US8] Reshape the edge client (wake word KEEP, VAD, audio I/O, pipeline client over WS) — `vocascade/edge/__main__.py` (from `satellite.py`).
- [ ] T242 [US8] Server transport endpoint replacing the retired FastAPI server; enforce the explicit transport-auth decision (device identity vs trusted-network, OQ-3) — `vocascade/transport/server.py`.
- [ ] T243 [P] [US8] Document per-hop latency budget + network-failure handling — `specs/006-custom-voice-pipeline-waterfall/quickstart.md`.

**Checkpoint**: deployable split topology, no accidental open endpoint.

---

## Phase 11: User Story 9 — Routing eval harness (Priority: P2)

**Goal**: Text-in → routing-out, headless, CI-able.

**Independent Test**: harness prints winning stage/skill/confidence + per-stage trace for an input; fixtures run green in CI.

> Recommended sequencing: this can run right after Phase 4 (it needs only the waterfall, not audio) so US2 is verifiable text-only earlier.

- [ ] T244 [US9] Text-in → routing-decision-out harness over the full waterfall (no audio/STT/TTS) — `vocascade/eval/route_harness.py`.
- [ ] T245 [P] [US9] Labeled fixtures + CI runner (≥50 utterances, ≥95% expected-stage) — `vocascade/eval/fixtures.jsonl`, `tests/test_routing_eval.py`.

**Checkpoint**: routing correctness measurable headlessly (SC-004/SC-009).

---

## Phase 12: User Story 10 — Context-divergence fix (Priority: P3)

**Goal**: Session-end gist to the memory service so local-only turns don't diverge memory.

**Independent Test**: after a locally-handled session, a summary POST is observed (when enabled); a failed POST never blocks teardown.

- [ ] T246 [US10] Session-end summary generation (local-LLM gist) + best-effort, non-blocking POST to the memory service — `vocascade/session/summary.py` (reuse `vocascade/pre_fetch_cache.py`/context source).
- [ ] T247 [P] [US10] Test: summary generated + POSTed on teardown; failure logged and non-blocking — `tests/unit/test_session_summary.py`.

**Checkpoint**: memory continuity preserved across local-only turns.

---

## Phase 13: Polish & Cross-Cutting Concerns

**Purpose**: Remove the last Pipecat remnants and update docs.

- [ ] T248 [P] Delete the Pipecat orchestration (`Pipeline`/`Runner`/`Task`/`FrameProcessor`) from the old adapter once all callers are migrated — remove `vocascade/adapter.py`.
- [ ] T249 [P] Update `README.md`, `AGENTS.md`, and the topology diagram for the `vocascade` single-package architecture and config split.
- [ ] T250 [P] Annotate `specs/004-pipecat-voice-adapter/tasks.md` items as superseded (Pipecat removed).
- [ ] T251 Full suite + 24-hour soak: no orphaned tasks, no unbounded registry/queue/skill-state growth (SC-001, SC-009, SC-011).

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 0 (gate)**: blocks Phase 2's Hermes/VAD/SDK-dependent work — must resolve OQ-1/OQ-2/OQ-3 first.
- **Phase 1 (Setup)**: T204 mechanical repackage blocks everything else (all later paths are `vocascade/`).
- **Phase 2 (Foundational)**: blocks all user stories.
- **Phases 3–12 (user stories)**: depend on Phase 2; then proceed in priority order. US1 (MVP) first; US3 depends on US1's pipeline + US2's router being present for the last stage; US4/US5 build on US1–US3; US6 builds on US2; US9 can be pulled forward right after US2.
- **Phase 13 (Polish)**: after the user stories that retire `adapter.py`'s callers.

### Within each user story

- Tests for the story may be written alongside or before implementation; routing/STOP/Hermes tests are required (not optional) for US2/US3/US5/US9.
- Types/models before stages before wiring.

### Parallel opportunities

- T202/T203 in Phase 0; T206/T207 in Phase 1; T212 in Phase 2.
- Within stories, `[P]` test tasks and the independent base-skills (T234/T235/T236) run in parallel.
- US9 (harness) can run in parallel with US4–US8 once US2 is done.

---

## Implementation Strategy

### MVP first

1. Phase 0 (gate) → Phase 1 (repackage + config) → Phase 2 (foundational ports) → **Phase 3 (US1)**.
2. **STOP and VALIDATE**: voice turn end-to-end, Pipecat gone. This is the first reviewable, demoable slice.

### Incremental delivery

US2 (routing+SDK) → US3 (Hermes hybrid path) → pull US9 (harness) forward to lock routing correctness → US4 (latency) → US5 (lifecycle/STOP/CONVERSE) → US6 (skills) → US7 (degradation) → US8 (split) → US10 (context-sync) → Polish. Each story is an independently testable increment; user works one branch per phase.

---

## Notes

- `[P]` = different files, no incomplete-task dependencies.
- The mechanical repackage (T204) MUST be its own commit — pure move, revertable, tests green before & after.
- Highest-risk task is T231 (STOP cancellation) — design the interrupt `asyncio.Event` into `pipeline.py` at T211, not retrofitted.
- Optional future (P3, NOT in scope here): explicit fire-and-forget delegation via "go do X / let me know when…" phrasing — an intent cue, not a duration heuristic; do not add a stream-vs-run branch.
- Total: T201–T251 (~51 tasks). MVP = Phases 0–3.
