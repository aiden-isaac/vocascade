# harness-as-skill — Design

## Context

The confidence waterfall's last stage is a hardcoded personal agent. `waterfall/stages/hermes.py`
contains two things: a trivial `HermesStage` (reports confidence 1.0 pointing at the `hermes`
skill) and `stream_hermes_reply()` — the dispatch-and-stream helper that actually belongs to the
skill that imports it (`base_skills/hermes.py`). Around it, `WaterfallRouter` special-cases the
string `"hermes"` five times:

1. `_STUB_STAGE_CLASSES["hermes"] = HermesStage`
2. `_threshold_for("hermes") → 0.0` (always clears)
3. `_ordered_stage_names` enforces HERMES-last
4. `resolve()` exhaustion fallback checks `s.name == "hermes"`
5. `from_config` drops the stage when `config.hermes_base_url` is empty (local-only mode, D2)

The stage's supposed unique powers are already SDK-shaped: `ctx.task_broker` (async dispatch) is
wired to every skill's context by the adapter, `ctx.notify` (proactive idle-gated delivery, US6)
exists, and skills stream sentence-by-sentence via async-generator handlers through the shared
TTS conveyor (#169). Capability negotiation (`HermesRunClient.probe_capabilities`) is internal
to the Hermes protocol client — a skill implementation detail, not core surface.

## Goals / Non-Goals

**Goals:**
- No personal agent named anywhere in `vocascade/waterfall/`.
- A generic `agent` fallback stage: routes to whatever skill `waterfall.agent_skill` names.
- A skill SDK contract sufficient for any agent skill: streaming handler + `ctx.task_broker` +
  `ctx.notify` + an `available` gate.
- `base_skills/hermes.py` keeps working unchanged in behavior, as the copyable reference.
- Existing configs keep working (legacy `hermes` stage name aliased).
- Local-only UX identical to de-personalize-byok: no agent → exhaustion speaks the can't-help
  notice. Do not re-solve.

**Non-Goals:**
- TTS conveyor changes (shipped, #169).
- Multi-session WS (v1.1).
- New agent integrations beyond the Hermes reference.
- Removing `task_broker.py` / `hermes_run_client.py` / hermes config keys from the repo — they
  are the reference skill's backend and the setup GUI / health probe keep using them.
- A generic capability-negotiation API in core.

## Decisions

### D1: `agent` stage name + `waterfall.agent_skill` config key
The stage list in `config.yaml` gains the generic name `agent`; a sibling key
`waterfall.agent_skill` (default `"hermes"`) names the claiming skill. Config over decorator
(`@skill(role="agent")` was the alternative) because exactly one skill may hold the role and
config is the natural single-owner slot — two skills declaring a decorator role would need a
tiebreaker. Legacy `hermes` in an existing stage list is aliased to `agent` with a one-line
deprecation warning in `from_config`; cheap, keeps live installs working.

### D2: `AgentStage` is the one generic stage class
Replaces `HermesStage`: `evaluate()` returns confidence 1.0 with `skill_name = agent_skill` when
that skill is registered, else 0.0. Threshold stays 0.0, ordering rule becomes
STOP-first/AGENT-last, `resolve()` exhaustion fallback targets the agent stage instance found in
`self.stages` (no string match against a personal name). Trace/eval output uses stage name
`agent`.

### D3: Availability is a zero-arg callable on `@skill`
`@skill(..., available=lambda: bool(os.getenv("HERMES_BASE_URL", "").strip()))`. `from_config`
builds the agent stage only when the claimed skill is registered AND `available()` is truthy
(absent callable ⇒ available). This generalizes the hardcoded `hermes_base_url` drop without
coupling user skills to `AdapterConfig` internals — a zero-arg callable can read env, probe a
file, whatever. Alternatives rejected: passing `AdapterConfig` in (couples third-party skills to
a frozen internal dataclass); always building the stage and letting the skill degrade at runtime
(regresses D2 of de-personalize-byok: agent-y utterances would say "I can't reach the agent"
instead of the can't-help notice).

### D4: `stream_hermes_reply` moves into the skill
Wholesale move into `base_skills/hermes.py`, including the chunker re-exports tests import.
It is Hermes-protocol glue (broker dispatch + live sink + `SpeechChunker`), and the reference
skill is exactly the file a stranger copies — the helper belongs in the template. No generic
"stream a live sink" SDK helper: it would have one caller.

### D5: SDK promotion is contractual for dispatch/delivery
`SkillContext.task_broker` keeps its name and wiring; its docstring/docs change from "present
only for the hermes skill" to "app-level async-run broker, available to any skill, None when no
backend is configured". `ctx.notify` is documented alongside. No new plumbing — both already
reach every skill invocation via the adapter.

### D6: Docs live in README + the reference skill's module docstring
A "Bring your own agent" section: drop `my_agent.py` into `user_skills/`, register with
`@skill(name=..., available=...)`, write an async-generator handler, set
`waterfall.agent_skill: my_agent`. Point at `base_skills/hermes.py` as the template. No separate
docs file; the pattern is one page.

## Risks / Trade-offs

- [Legacy `hermes` stage name in live configs] → alias to `agent` + deprecation warning in
  `from_config`; `config.yaml.example` updated to the new form.
- [Eval fixtures / route harness assert the `hermes` stage name] → update fixtures to `agent`
  in the same PR; the harness itself is name-agnostic (traces whatever the stage reports).
- [A user sets `agent_skill` to a nonexistent skill] → stage not built, logged at startup;
  behavior degrades to local-only exhaustion (already-speced UX), never silence.
- [`available()` raising] → treated as unavailable with a logged warning; startup must not
  crash on a bad user skill (matches existing user-skill import isolation).
- [Zero-arg `available` can't see per-skill config.yaml block] → acceptable; skills read env or
  their own sources. If real demand appears, pass the skill's config dict later — additive.

## Migration Plan

Single PR. Users with `- hermes` in `config.yaml` see a deprecation warning and identical
behavior; `.env` untouched (`HERMES_BASE_URL` semantics unchanged, now enforced by the reference
skill's `available` instead of a router special case). Rollback = revert the PR; no data or
protocol changes (WS protocol version untouched).

## Open Questions

None blocking. Deferred: whether `available` should ever receive the skill's config block
(additive if needed).
