# Data Model: Custom Voice Pipeline & Confidence Waterfall

Entities are in-memory dataclasses/enums (no database). Field types are indicative.

## WaterfallStage (abstract)

The routing unit. Concrete stages: `StopStage`, `ConverseStage`, `HighStage`, `MediumStage`, `SmalltalkStage`, `HermesStage`.

| Field / Method | Type | Notes |
|----------------|------|-------|
| `name` | str | Stable identifier used in config and the eval trace. |
| `threshold` | float | Minimum confidence for this stage's result to win. Config-overridable. |
| `enabled` | bool | From `config.yaml`; disabled stages are skipped. |
| `evaluate(utterance, ctx) -> ConfidenceResult` | async | Returns this stage's confidence + winning handler for the utterance. |

**Rules**: STOP is always first, HERMES always last (FR-011). The router walks enabled stages in order; the first `ConfidenceResult` whose `confidence >= threshold` wins; ties resolve to the earlier stage.

## ConfidenceResult

A stage's verdict for one utterance.

| Field | Type | Notes |
|-------|------|-------|
| `stage` | str | Stage name that produced it. |
| `confidence` | float | 0.0–1.0. Medium-stage values are clamped to the medium band (OQ-5). |
| `skill_name` | str \| None | Winning skill/handler, if any. |
| `payload` | dict | Stage-specific data (e.g. extracted entities, classifier raw output). |

## Skill

A registered handler. Created by the `@skill(...)` decorator and held in the `SkillRegistry`.

| Field | Type | Notes |
|-------|------|-------|
| `name` | str | Unique. |
| `examples` | list[str] | Phrases feeding the medium-stage classifier prompt (capped per OQ-5). |
| `keywords` | list[str] | Fast high-stage matching. |
| `handler` | async callable | `handler(intent, entities, ctx) -> str` (spoken text) or a stream. |
| `confidence` | callable \| None | Optional custom scorer; smalltalk returns a fixed floor (0.35). |
| `config` | dict | Per-skill settings (enabled, provider, filler, thresholds) from `config.yaml`. |
| `source` | enum(`bundled`, `user`) | Bundled base skills vs. auto-discovered user skills. |

**Rules**: A user-skill import error isolates to that skill (logged, skipped) without aborting startup (FR-022, edge case).

## SkillContext

The per-invocation bundle handed to every skill handler (FR-021).

| Field | Type | Notes |
|-------|------|-------|
| `tools` | ToolBag | Integration clients (todoist, calendar, home assistant, etc.). |
| `session` | SessionState | Current session view. |
| `history` | list[Turn] | Recent voice turns. |
| `config` | UserConfig | Resolved user/skill config. |
| `emit_filler` | async callable | Lets a skill trigger its own filler audio. |
| `local_llm` | LocalLLM | For smalltalk and optimistic openings only (never Hermes tool schemas). |

## SessionState

| Field | Type | Notes |
|-------|------|-------|
| `state` | enum(`passive`, `active`, `speaking`) | Lifecycle (FR-060). |
| `converse_claim` | ConverseClaim \| None | Active multi-turn claim, if any. |
| `interrupt` | asyncio.Event | The single barge-in/STOP signal (FR-002, FR-071). |
| `voice_session_id` | str | Per-session conversational identity (`X-Hermes-Session-Id`). |
| `wake_count` | int | Wake activations this session. |
| `last_activity_at` | float | Drives the silence timeout. |

**Transitions**: `passive --wakeword--> active --transcription--> (handling) --tts--> speaking --done--> active`; `active --farewell|silence_timeout--> passive` (in-flight background tasks retained, FR-061); any state `--stop--> active` after cancelling in-flight work (FR-070).

## ConverseClaim

| Field | Type | Notes |
|-------|------|-------|
| `skill_name` | str | Skill awaiting the next utterance. |
| `prompt` | str | The follow-up question asked. |
| `expires_at` | float | Timeout after which the claim auto-releases (edge case). |
| `resume` | async callable | Invoked with the next utterance when the claim wins CONVERSE. |

## HermesTask (retained from 005 — unchanged)

The async agent run record. Carried forward verbatim; listed for completeness.

| Field | Type | Notes |
|-------|------|-------|
| `task_id` | str | `task_YYYYMMDD_HHMMSS_NN`. |
| `run_id` | str \| None | Server-issued; None until accepted. |
| `state` | enum(`pending`,`executing`,`completed`,`failed`,`cancelled`) | Idempotent state machine. |
| `request_text` | str | Originating utterance. |
| `result_text` | str \| None | Full result once terminal. |
| `session_id` | str | Dispatching voice session. |
| `delivered` | bool | Result spoken (or spoken-interrupted). |

**Note**: 006 adds streamed delivery (consuming `message.delta` events as they arrive) on top of this existing run lifecycle; the task model itself does not change.

## RoutingDecision (eval harness)

The record the text-only harness emits per input (FR-121).

| Field | Type | Notes |
|-------|------|-------|
| `input` | str | The utterance text. |
| `winning_stage` | str | Stage that won. |
| `winning_skill` | str \| None | Skill that won. |
| `confidence` | float | Winning confidence. |
| `trace` | list[ConfidenceResult] | Per-stage results in evaluation order. |
| `expected` | dict \| None | From the fixtures file, for pass/fail in CI. |
