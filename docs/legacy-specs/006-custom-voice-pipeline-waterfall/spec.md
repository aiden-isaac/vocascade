# Feature Specification: Custom Voice Pipeline & Confidence Waterfall

**Feature Branch**: `006-custom-voice-pipeline-waterfall`

**Created**: 2026-06-13

**Status**: Draft

**Input**: User description: "Replace the Pipecat framework with a custom asyncio voice pipeline and an OVOS-inspired confidence-waterfall skill router; consolidate the codebase into one package (`vocascade`); make the Hermes agent the always-async last stage with streamed delivery; add a modular skill SDK, session lifecycle, hard STOP, multi-turn CONVERSE, latency masking, satellite/server split, graceful degradation, a routing eval harness, and Honcho context-sync."

## Context

This feature supersedes the framework choice made in **004-pipecat-voice-adapter** and extends the backend built in **005-hermes-agent-backend** (Phases 0–2 merged).

004 delivered a working voice loop built on the **Pipecat** framework. In practice Pipecat assumes a single-LLM "brain", which fought this project's permanent **two-brain** design — a fast local LLM for conversation plus a heavy remote Hermes agent for real work. That mismatch produced recurring friction: a brittle `ENDSESSION` sentinel hack, processor-ordering bugs between the adapter and teardown stages, and awkward server-side VAD handling. Pipecat is also a heavy dependency that raises the barrier for open-source contributors and makes swapping STT/TTS/LLM components harder.

006 removes Pipecat entirely and replaces it with a purpose-built, dependency-light voice pipeline and an OVOS-inspired **confidence waterfall** that routes each utterance to the cheapest capable handler, with the Hermes agent as the always-last stage. The two-brain split is **permanent and unchanged**. All framework-agnostic 005 machinery (the `/v1/runs` async API client, task broker, proactive delivery coordinator, cancellation rules) is retained; 006 layers token-streamed delivery onto that existing run path rather than adding a parallel synchronous path.

The codebase is also consolidated. Today there are two packages: `voice_adapter/` (the active Pipecat app) and `voice_satellite/` (an older server that doubles as a shared library). 006 collapses both into a single package named **`vocascade`** and deletes the old `voice_satellite/` package.

## User Scenarios & Testing *(mandatory)*

### User Story 0 — Pipecat removal & single-package consolidation (Priority: P0)

As a maintainer, I want the framework dependency removed and the codebase collapsed into one well-structured package so that contributors face a small, legible surface and components can be swapped freely.

**Why this priority**: Nothing else in this feature can be built while the Pipecat framework still owns orchestration and the code is split across two packages. This is the enabling, structure-only foundation.

**Independent Test**: After this story, `grep -ri "pipecat" vocascade/` and `grep -ri "voice_satellite" .` both return nothing, the old `voice_satellite/` directory no longer exists, the `pipecat-ai` dependency is gone, and the previously-passing module tests still pass.

**Acceptance Scenarios**:

1. **Given** the repo on `main` with two packages, **When** the consolidation is complete, **Then** a single `vocascade/` package contains all retained modules and `voice_satellite/` has been deleted with every import updated.
2. **Given** the Pipecat-coupled orchestration is removed, **When** the test suite runs, **Then** all retained-module tests pass and no module imports `pipecat`.
3. **Given** the Genie TTS path is reshaped off Pipecat's `TTSService`, **When** the TTS server's first audio chunk takes 4–7 seconds, **Then** the utterance is still spoken in full (the slow-first-chunk tolerance that Pipecat's audio-context drain previously provided is preserved).

---

### User Story 1 — Speak end-to-end through the custom pipeline (Priority: P1) 🎯 MVP

As a user, I want to say the wake word, ask a simple question, and hear a spoken answer — produced entirely by the new pipeline with no framework involved.

**Why this priority**: This is the MVP: it proves the raw-asyncio loop (wake word → VAD → STT → router → TTS → speaker) fully replaces Pipecat for a single turn. Everything else builds on it.

**Independent Test**: Say the wake word, ask a smalltalk question, and hear a persona-appropriate spoken reply, with no `pipecat` import anywhere in the execution path.

**Acceptance Scenarios**:

1. **Given** the device is in passive (wake-word-only) listening, **When** the user speaks the wake word, **Then** the device emits an audible acknowledgement and enters active listening.
2. **Given** active listening, **When** the user asks a conversational question, **Then** the utterance is transcribed, routed to a local handler, synthesized, and played back as speech.
3. **Given** the bot is speaking a reply, **When** the user begins speaking over it, **Then** playback stops promptly (barge-in).

---

### User Story 2 — Confidence-waterfall routing & skill SDK (Priority: P1)

As a user, I want my request handled by the cheapest capable skill, and as a contributor I want to add skills declaratively so that the assistant is fast and extensible.

**Why this priority**: The waterfall is the core routing brain and the skill SDK is the extension surface; together they turn the MVP loop into a real, extensible assistant. Required before latency masking and bundled skills.

**Independent Test**: Run the text-only routing harness (US9) over a fixtures file; each utterance resolves to the expected stage and skill with the expected confidence ordering.

**Acceptance Scenarios**:

1. **Given** registered skills with keyword matches, **When** an utterance clearly matches one, **Then** the high-confidence stage wins immediately without consulting slower stages.
2. **Given** an utterance with no keyword match, **When** routing proceeds, **Then** the medium stage's local-LLM classifier scores candidate skills and the highest qualifying score wins.
3. **Given** a conversational utterance no skill claims, **When** routing reaches the fallback, **Then** the bundled smalltalk skill (fixed low confidence) handles it.
4. **Given** an utterance that needs personal data, real-time information, or an external action (e.g. "what are my tasks today"), **When** routing reaches the smalltalk floor, **Then** the smalltalk gate abstains and the utterance falls through to the Hermes stage rather than being answered "I don't have access" from general knowledge (FR-033).
5. **Given** a new skill is registered with example phrases, **When** the app starts, **Then** the medium-stage classifier prompt is regenerated to include those examples with no manual prompt editing.

---

### User Story 3 — Hermes agent as the always-async last stage (Priority: P1)

As a user, I want requests that need real data or external action to go to the Hermes agent and have its answer spoken back as soon as words are ready, whether the task is quick or slow.

**Why this priority**: Restores 005's value (the heavy brain) within the new architecture, and resolves the riskiest design question by collapsing it to one path: every Hermes query is an async run; responsiveness comes from streaming, not from a fragile per-query stream-vs-dispatch decision.

**Independent Test**: A request that needs the agent dispatches a run; its incremental output is streamed into TTS for a prompt-emitting run, and a slow run's result is delivered proactively when it completes — both via the same machinery, with no branching heuristic.

**Acceptance Scenarios**:

1. **Given** an utterance no local skill claims, **When** routing reaches the last stage, **Then** the request is dispatched as an asynchronous Hermes run (never a synchronous call).
2. **Given** a dispatched run that emits incremental output, **When** output tokens arrive, **Then** they are streamed into TTS so the first spoken words begin promptly.
3. **Given** a run that completes after the conversation has moved on, **When** its result arrives, **Then** the proactive delivery coordinator speaks it at the next idle moment.
4. **Given** a streamed run whose event stream drops mid-output, **When** the drop is detected, **Then** the system reconciles via a run-status snapshot and still delivers the terminal result.

---

### User Story 4 — Latency masking (Priority: P2)

As a user, I want the assistant to feel instant — never dead air while it decides or fetches.

**Why this priority**: Routing correctness alone can still feel slow; masking makes the experience feel responsive. It depends on the waterfall (US2) and Hermes stage (US3).

**Independent Test**: For each routing outcome, observe that the appropriate masking behavior fires — no filler for instant matches, a dynamically generated spoken filler before a tool call, a generated opening before streamed agent output, escalating follow-ups while a slow run keeps producing nothing, and an instant pre-rendered acknowledge clip on the wakeword.

**Acceptance Scenarios**:

1. **Given** a high-confidence instant skill match, **When** it is handled, **Then** no filler audio is played (the answer is already immediate).
2. **Given** a medium-stage tool skill, **When** the tool runs, **Then** a dynamically generated spoken filler plays and the response streams as it becomes available.
3. **Given** a Hermes request, **When** the run is dispatched, **Then** a dynamically generated query-appropriate opening plays and the run's streamed output continues the utterance naturally.
4. **Given** a dispatched run that produces no output for a while, **When** the configured interval elapses, **Then** an escalating "still working" follow-up is spoken (repeating at a configurable, optionally backing-off interval up to a cap) until output begins streaming.
5. **Given** the wakeword fires, **When** the session activates, **Then** a pre-rendered acknowledgement clip plays instantly (the only pre-rendered audio in use; all masking fillers are generated on the fly).

---

### User Story 5 — Session lifecycle, STOP, and multi-turn CONVERSE (Priority: P2)

As a user, I want conversations to start and end cleanly, to be able to interrupt with "stop" at any moment, and to answer a skill's follow-up question naturally.

**Why this priority**: These are the three control-flow gaps the old Pipecat sentinel handled poorly. They make the assistant feel reliable and conversational.

**Independent Test**: Drive the session through passive → active → speaking → passive transitions; say "stop" mid-TTS, mid-skill, and mid-Hermes-run and confirm each cancels; have a multi-turn skill claim and consume the next utterance.

**Acceptance Scenarios**:

1. **Given** an active session, **When** the user clearly signals they are finished (farewell) or the session times out in silence, **Then** the device returns to passive listening and any in-flight background tasks are retained, not killed.
2. **Given** the assistant is speaking, a skill is executing, or a Hermes run is in progress, **When** the user says "stop", **Then** that activity is cancelled within a fraction of a second and the device returns to listening.
3. **Given** a skill asks a follow-up question (e.g., "for how long?"), **When** the user replies, **Then** that reply is routed to the waiting skill ahead of the normal waterfall.
4. **Given** a multi-turn skill is awaiting a reply, **When** the user instead says "stop" or stays silent past a timeout, **Then** the skill's claim is released and the session continues normally.

---

### User Story 6 — Bundled skills, user skills, and per-skill config (Priority: P2)

As a contributor, I want to add a skill by dropping a file in a folder and editing config, with no changes to audio or pipeline code; as a user, I want useful built-in skills (timers, date/time, stop).

**Why this priority**: Delivers the concrete extensibility promise and ships everyday utility. Builds on the skill SDK (US2).

**Independent Test**: Add a new skill file to the user-skills directory and a config entry; confirm it is auto-discovered and routes correctly via the harness, with zero edits to pipeline/STT/TTS code.

**Acceptance Scenarios**:

1. **Given** the bundled skills, **When** the user asks for a timer, the date, or the time, **Then** the corresponding skill responds.
2. **Given** a new skill file placed in the user-skills directory, **When** the app starts, **Then** the skill is discovered, registered, and routable.
3. **Given** a skill is disabled in config, **When** the app starts, **Then** that skill is not registered and does not participate in routing.
4. **Given** a user skill that raises an error at import time, **When** the app starts, **Then** that one skill is skipped with a logged warning and startup continues.

---

### User Story 7 — Graceful degradation at every stage (Priority: P2)

As a user, I never want silent failures — if a tool, the agent, or the server is unavailable, I want to be told, and I want whatever still works to keep working.

**Why this priority**: Edge devices run on unreliable networks; resilience is a constitutional requirement. Cross-cuts every stage.

**Independent Test**: Force each failure mode (a skill's tool call fails, Hermes unreachable, server unreachable from the edge) and confirm a defined, spoken-or-surfaced fallback rather than a crash or silent drop.

**Acceptance Scenarios**:

1. **Given** a skill whose tool call fails, **When** routing handles it, **Then** the user hears a graceful error and routing may fall through to a lower stage rather than failing silently.
2. **Given** Hermes is unreachable, **When** a request would route to it, **Then** the user hears a clear spoken notice and the local loop keeps functioning.
3. **Given** the edge device cannot reach the server, **When** the user speaks, **Then** the device surfaces a clear status and degrades per policy rather than hanging.

---

### User Story 8 — Satellite/server component split (Priority: P2)

As an operator, I want a clear, configurable boundary between what runs on the edge device and what runs on the server, with documented latency budgets and network-failure behavior.

**Why this priority**: The deployment topology (edge box vs. server) determines latency and failure handling and must be explicit, not accidental.

**Independent Test**: Configure the edge and server roles via config (no hardcoded hosts); confirm the edge runs wake word + VAD + audio I/O + the pipeline client and the server runs STT + waterfall + local LLM + TTS + Hermes, and that a network partition is handled per documented policy.

**Acceptance Scenarios**:

1. **Given** a configured edge/server split, **When** the system starts, **Then** each component runs on its assigned role with hosts taken from configuration.
2. **Given** the transport between edge and server, **When** it is established, **Then** an explicit authentication decision is in force (device identity or a documented trusted-network boundary), not an accidental open endpoint.
3. **Given** a mid-utterance network partition, **When** it occurs, **Then** the edge surfaces a clear status and recovers per documented policy.

---

### User Story 9 — Text-in → routing-decision-out eval harness (Priority: P2)

As a contributor, I want to verify routing decisions from plain text inputs without running microphones, STT, or TTS, so that skills can be tested quickly and in CI.

**Why this priority**: Makes routing testable in isolation — the practical means of validating US2/US6 — and is CI-friendly for an open-source project.

**Independent Test**: Feed a fixtures file of utterances to the harness; for each it prints the winning stage, winning skill, confidence, and the per-stage confidence trace, and the run is green in a headless environment.

**Acceptance Scenarios**:

1. **Given** an utterance string, **When** the harness resolves it, **Then** it outputs the winning stage, skill, confidence, and full per-stage trace with no audio components engaged.
2. **Given** a fixtures file of labeled utterances, **When** the harness runs in CI, **Then** it passes/fails against the expected routing decisions.

---

### User Story 10 — Context-divergence fix via session summary (Priority: P3)

As a user, I want the agent's long-term memory to stay consistent even when some of my requests were answered locally, so the assistant doesn't "forget" context handled by skills.

**Why this priority**: A correctness/quality refinement for memory continuity; valuable but not required for the assistant to function.

**Independent Test**: After a session that included locally-handled turns, confirm a short natural-language session summary is POSTed to the memory service at session end, and that a failure to POST does not block teardown.

**Acceptance Scenarios**:

1. **Given** a session with locally-handled turns and the memory service enabled, **When** the session ends, **Then** a concise natural-language summary (gist, not transcript) is sent to the memory service.
2. **Given** the memory service is unreachable, **When** the session ends, **Then** teardown completes normally and the failure is logged.

---

### Edge Cases

- Two stages both clear their thresholds for the same utterance → the earlier stage in waterfall order wins (deterministic).
- The medium-stage classifier returns malformed or out-of-band output → its score is clamped to the medium band or the stage is skipped; never a crash.
- A user-skill file raises at import → that skill is skipped with a logged warning; other skills and startup proceed.
- "Stop" arrives in the gap between selecting a response and starting playback → the about-to-start response is still cancelled.
- A multi-turn skill claims the next utterance but never releases it → the claim times out and the waterfall resumes.
- A Hermes run's streamed output drops mid-token → reconcile via a run-status snapshot; the terminal result is still delivered.
- A silence timeout and a farewell signal race at session end → a single, idempotent teardown.
- `config.yaml` is present but malformed → fail fast with a clear, located error.
- The wake word fires while a previous proactive result is mid-delivery → the in-flight delivery is interrupted or the new turn is queued per the delivery gate.
- A network partition between edge and server occurs mid-utterance → the edge surfaces a clear status and does not hang.

## Requirements *(mandatory)*

### Functional Requirements

**Pipeline & packaging**

- **FR-001**: The system MUST orchestrate the voice loop (audio in → wake word → VAD → STT → router → TTS → audio out) with a custom asyncio implementation and MUST NOT import any third-party voice-pipeline framework.
- **FR-002**: Barge-in MUST be signalled through a single interrupt mechanism observable by every stage and by the TTS output, so the user can interrupt by speaking.
- **FR-003**: All retained code MUST live in a single package named `vocascade`; the legacy `voice_satellite` package MUST be removed and all imports migrated.
- **FR-004**: The `pipecat-ai` dependency MUST be removed; remaining audio/ML dependencies are the underlying libraries (speech-to-text, voice-activity detection, wake-word, text-to-speech client).
- **FR-005**: The Genie TTS output MUST tolerate a slow first audio chunk (several seconds) without dropping the utterance.

**Confidence waterfall**

- **FR-010**: The router MUST evaluate an ordered set of stages where each stage returns a numeric confidence, and the first stage whose result clears its threshold wins.
- **FR-011**: The stage order MUST place a STOP/system stage first and the Hermes stage last, with CONVERSE, high-confidence skills, medium-confidence skills, and smalltalk in between.
- **FR-012**: A high-confidence stage MUST resolve via fast deterministic matching (keyword/pattern) and return quickly enough to be imperceptible.
- **FR-013**: A medium-confidence stage MUST classify intent using the local LLM, and its classifier prompt MUST be generated automatically at startup from the registered skills' example phrases.
- **FR-014**: The waterfall order, thresholds, and per-stage enablement MUST be configurable.
- **FR-015**: Each stage MUST be independently importable and testable without instantiating the full pipeline.

**Skill SDK**

- **FR-020**: The system MUST provide a declarative skill registration mechanism (name, example phrases, keywords) that populates a skill registry.
- **FR-021**: Each skill handler MUST receive a context object exposing tools, session state, recent history, user configuration, a filler-emitting capability, and access to the local LLM.
- **FR-022**: The system MUST ship bundled skills (at minimum: smalltalk, timers, date/time, stop) and MUST auto-discover user-provided skills from a designated directory at startup.
- **FR-023**: Per-skill settings (enabled, provider, filler text, thresholds) MUST be configurable.
- **FR-024**: A contributor MUST be able to add a working skill by adding a file in the user-skills directory and a config entry, with no changes to pipeline, STT, or TTS code.

**Smalltalk & local-LLM scope**

- **FR-030**: A bundled smalltalk skill MUST act as a fixed low-confidence fallback that wins only when nothing above it scored higher — and, per FR-033, only when the utterance is genuinely conversational, so it does not starve the Hermes stage below it.
- **FR-031**: Smalltalk replies MUST be generated by the local LLM in the assistant's configured persona.
- **FR-032**: The local LLM's responsibilities MUST be limited to smalltalk replies, the medium-stage classifier, the smalltalk routing gate (FR-033), and optimistic partial openings; it MUST NOT be given the Hermes/skill tool schemas.
- **FR-033**: Because the smalltalk floor sits directly above the Hermes fallback, the smalltalk stage MUST apply a content-aware gate so it abstains (yielding to the Hermes stage) for utterances that need the user's personal data, real-time/external information, or an external action, and claims only genuinely conversational or general-knowledge utterances. The gate MUST be configurable per skill (`smalltalk.gate`) and MUST degrade safely to the plain floor when the local LLM is unavailable, never silently dropping the turn.

**Latency masking**

- **FR-040**: Latency masking MUST be a layer distinct from routing.
- **FR-041**: A high-confidence instant match MUST play no filler.
- **FR-042**: A medium-stage tool skill MUST play a dynamically generated spoken filler before its result and then stream the response.
- **FR-043**: A Hermes request MUST play a dynamically generated query-appropriate opening and then stream the run's incremental output into TTS.
- **FR-044**: Masking fillers MUST be generated on the fly and spoken via TTS (never pre-rendered clips), favoring short, voice-optimized lines; the local-LLM filler prompt MUST be constrained so a filler never answers the request, asks a question, or starts a conversation.
- **FR-045**: While a slow stage produces no output, the system MUST emit progressive "still working" follow-up fillers at a configurable interval (with optional back-off and a cap), stopping as soon as output begins streaming.
- **FR-046**: A pre-rendered acknowledgement clip MUST play instantly when the wakeword fires; this is the ONLY pre-rendered audio in use.
- **FR-047**: The filler content source (pooled phrases / local-LLM / hybrid) and cadence (interval, back-off, cap) MUST be configurable.

**Hermes stage (always-async + streamed delivery)**

- **FR-050**: Every request routed to the Hermes stage MUST be dispatched as an asynchronous run; there MUST be no synchronous Hermes path and no per-query stream-vs-dispatch heuristic.
- **FR-051**: The run's incremental output events MUST be streamed into TTS as they arrive for responsiveness.
- **FR-052**: Results that arrive late (after the conversation has moved on) MUST be delivered by the proactive delivery coordinator at an idle moment.
- **FR-053**: The existing async runs API, task lifecycle/state machine, cancellation guard, journal, and proactive-delivery rules from 005 MUST be preserved unchanged; streamed delivery is layered onto that run path.

**Session lifecycle**

- **FR-060**: The system MUST define explicit session states and transitions (passive → active → speaking → passive) with documented triggers.
- **FR-061**: At session end (farewell or silence timeout), in-flight background tasks MUST be retained, not cancelled.
- **FR-062**: The end-of-session mechanism MUST be reimplemented for the custom pipeline, retaining both a model-emitted end signal and a deterministic farewell-phrase backstop, and MUST be disarmed if the user re-engages mid-farewell.

**STOP / hard interrupt**

- **FR-070**: A STOP action MUST take effect in all states — while the assistant is speaking, while a skill is executing, and while a Hermes run is in progress.
- **FR-071**: STOP MUST propagate cancellation cleanly to all in-flight work (TTS output, the active skill, the Hermes run/stream consumer) without leaving orphaned tasks.

**CONVERSE (multi-turn)**

- **FR-080**: An active multi-turn skill MUST be able to claim the next utterance ahead of the normal waterfall.
- **FR-081**: A claim MUST be released on completion, on timeout, or on STOP.

**Context-sync**

- **FR-090**: At session end, the system MUST send a concise natural-language session summary (gist, not transcript) to the memory service when it is enabled.
- **FR-091**: The summary send MUST be best-effort: failure MUST be logged and MUST NOT block session teardown.

**Degradation**

- **FR-100**: Each stage MUST define a fallback when its handler's tool call fails (degrade to a lower stage or speak a graceful error) — no silent failure.
- **FR-101**: When Hermes is unreachable, the user MUST hear a clear notice and the local loop MUST keep functioning.
- **FR-102**: When the server is unreachable from the edge, the edge MUST surface a clear status and degrade per policy.

**Component split**

- **FR-110**: The deployment MUST support splitting components between an edge role (wake word, VAD, audio I/O, pipeline client) and a server role (STT, waterfall, local LLM, TTS, Hermes), driven by configuration with no hardcoded hosts.
- **FR-111**: The edge↔server transport MUST carry an explicit authentication decision (device identity or a documented trusted-network boundary).

**Eval harness**

- **FR-120**: The system MUST provide a text-input → routing-decision-output harness that resolves the full waterfall without engaging audio, STT, or TTS.
- **FR-121**: For each input, the harness MUST report the winning stage, the winning skill and its confidence, and the per-stage confidence trace, and MUST be runnable headless in CI against a fixtures file.

**Configuration**

- **FR-130**: Structural configuration (waterfall order, per-skill settings, thresholds) MUST live in a `config.yaml`; secrets and service URLs MUST remain in environment configuration; an example config MUST be maintained.
- **FR-131**: The system MUST fail fast with a clear, human-readable message when required configuration is missing or malformed.

### Key Entities

- **WaterfallStage**: An ordered routing step with a name and threshold that, given an utterance and context, produces a confidence result.
- **ConfidenceResult**: A stage's verdict — a numeric confidence plus the winning skill/handler and any payload and trace data.
- **Skill**: A registered handler with a name, example phrases, keywords, configuration, and an async handler function.
- **SkillContext**: The per-invocation bundle handed to a skill handler (tools, session state, history, config, filler emitter, local LLM).
- **SessionState**: The current lifecycle state, any active CONVERSE claim, the interrupt signal, and the conversational session identity.
- **HermesTask**: The retained 005 representation of an async agent run (id, run id, state, request, result, delivery status) — unchanged.
- **RoutingDecision**: The eval-harness record for one input — winning stage, winning skill, confidence, and full trace.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `grep -ri "pipecat" vocascade/` returns zero matches, the `pipecat-ai` dependency is absent, `grep -ri "voice_satellite" .` returns zero matches, the `voice_satellite/` directory no longer exists, and the retained test suite passes.
- **SC-002**: A high-confidence local skill produces its first spoken response within the prior loop's budget (well under 3 seconds end-to-end), and high-confidence routing decisions are imperceptibly fast.
- **SC-003**: When a Hermes run emits output promptly, the first spoken words begin within roughly half a second of the run starting; slow runs degrade gracefully to proactive delivery.
- **SC-004**: Over a fixtures set of at least 50 labeled utterances, the waterfall selects the expected stage and skill at least 95% of the time, verified headlessly via the eval harness.
- **SC-005**: A contributor can add a new skill (a file in the user-skills directory plus a config entry) and have it route correctly with zero edits to pipeline, STT, or TTS code.
- **SC-006**: A STOP command cancels active speech, an active skill, and an in-flight Hermes run within a fraction of a second in all three states, leaving no orphaned tasks.
- **SC-007**: Adding a skill with new example phrases changes the medium-stage classifier prompt automatically at the next startup, with no code edits.
- **SC-008**: Every Hermes request goes through the async run path; a run that completes after the conversation moved on is still delivered proactively, and two concurrent runs complete without cross-task mix-ups.
- **SC-009**: The routing eval harness runs green in a headless CI environment with no audio devices present.
- **SC-010**: After a session with locally-handled turns, a session summary is sent to the memory service when enabled, and a send failure never blocks teardown.
- **SC-011**: A 24-hour soak produces no orphaned tasks and no unbounded growth of the task registry, delivery queue, or skill state.

## Assumptions

- The two-brain split (fast local LLM for conversation, heavy remote Hermes agent for real work) is permanent and out of scope for revisiting.
- The local LLM is never given the Hermes/skill tool schemas; its role is strictly smalltalk, classification, and optimistic openings.
- The remote Hermes agent is reachable over the network (currently a remote host) and exposes the async runs API pinned in 005; 006 assumes that contract is unchanged and re-affirms it.
- Edge VAD already segments speech in the current edge client; whether VAD also runs authoritatively on the server is resolved in research before adding a server-side VAD dependency.
- `config.yaml` is an acceptable configuration mechanism per the project constitution (which permits a central config file), used alongside environment configuration for secrets.
- The retained 005 modules (run client, task broker, delivery coordinator, transcript manager, filler engine, pre-fetch cache) are framework-agnostic and carried forward into `vocascade` largely as-is.
- Test tasks are included for routing, session control, and the Hermes stage because correctness of routing and cancellation is central to this feature.

## Deferred from 005 / Out of Scope

005 (Hermes agent backend) merged Phases 0–2 (the MVP voice query + proactive result). Its later phases were written against the now-removed Pipecat adapter, so they are **superseded as written**. 006 keeps and extends 005's framework-agnostic foundation (runs API client, task broker, delivery coordinator, transcript manager) but does **not** re-specify the capabilities below. They remain valuable but are **not built and out of scope for 006** unless explicitly added — the implementing agent should fold any that still matter into a later 006 phase or a follow-up spec, now built on the `vocascade`/waterfall foundation rather than the old adapter:

- **Remote context hydration** — reading the agent's `USER.md`/`MEMORY.md` into the prompt via `HERMES_CONTEXT_SOURCE` (`file://` watch or `ssh://` SFTP poll). Note: 006's US10 is the *opposite* direction (writing a session summary *to* the memory service), not hydration *from* it. `pre_fetch_cache.py` is retained in the layout but left unwired.
- **Offline queue / handler** — `LITELLM_HEALTH_URL` health detection, the `OFFLINE_QUEUE_PATH` disk queue, and the night-window deferral.
- **Task journal persistence** — a durable `TASK_JOURNAL_PATH` journal so tasks survive restarts (the module is retained but no 006 task wires it up).
- **Voice approval flow** — the spoken yes/no resolution of agent approval requests. The delivery coordinator still *delivers* `APPROVAL_REQUEST` items and `resolve_approval()` exists in the run client, but the conversational resolve loop is not specified here.
