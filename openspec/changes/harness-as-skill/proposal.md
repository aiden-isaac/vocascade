# harness-as-skill

## Why

The waterfall still hardcodes one personal agent: `waterfall/stages/hermes.py` and five
`"hermes"` special cases inside `WaterfallRouter` (stage map, threshold, STOP-first/HERMES-last
ordering, exhaustion fallback, local-only drop). Everything else in the core is already
de-personalized (BYOK LLM, local-only mode). Promoting the stage's remaining unique powers into
the skill SDK and deleting it means ANY agent — Hermes, OpenClaw, LangGraph — is just a skill
file dropped into `user_skills/` that claims the fallback role in config. Cleanup + ecosystem
enablement, not a beta blocker.

Audit result (what the stage does that a plain skill can't):

1. **Async dispatch** — `ctx.task_broker` already exists on `SkillContext` and is wired to every
   skill; only its contract says "hermes only". Promotion is contractual, not plumbing.
2. **Proactive late delivery** — `ctx.notify` (idle-gated, app-level DeliveryCoordinator) already
   exists (US6). Nothing to build.
3. **Capability negotiation** — lives inside `HermesRunClient.probe_capabilities()`; it is the
   skill's protocol business, nothing to promote into core.
4. **The genuinely missing piece** — a generic way for the router to know whether the configured
   agent skill is usable (today hardcoded as "drop hermes stage when `HERMES_BASE_URL` empty"),
   and `stream_hermes_reply()` living in the waterfall package instead of the skill.

## What Changes

- **BREAKING (config)**: waterfall stage name `hermes` is replaced by a generic `agent` stage;
  a new `waterfall.agent_skill` key (default `hermes`) names the skill that claims the fallback
  role. Legacy `hermes` in an existing `config.yaml` stage list is aliased to `agent` with a
  deprecation warning — existing installs keep working.
- Delete `vocascade/waterfall/stages/hermes.py`. `stream_hermes_reply()` and the chunker
  re-exports move into `vocascade/skills/base_skills/hermes.py` (the reference agent skill).
- Generalize `WaterfallRouter`: `AgentStage` routes to whatever skill `waterfall.agent_skill`
  names; ordering rule becomes STOP-first/AGENT-last; exhaustion fallback targets the agent stage
  generically; local-only drop is driven by skill availability, not `HERMES_BASE_URL`.
- Skill SDK: `@skill` gains an optional zero-arg `available` callable. The agent stage is built
  only when the claimed skill is registered and available; otherwise local-only exhaustion
  behavior (unchanged from de-personalize-byok) applies.
- `SkillContext.task_broker` and `ctx.notify` documented as stable SDK surface for any skill.
- `base_skills/hermes.py` stays as the working reference implementation — the template a
  stranger copies to wire up their own agent.
- Document the bring-your-own-agent pattern (drop `my_agent.py` in `user_skills/`, set
  `waterfall.agent_skill`).

## Capabilities

### New Capabilities
- `agent-fallback`: the waterfall's generic last-resort stage — how a skill claims the agent
  role via config, ordering/exhaustion rules, availability-driven local-only behavior.
- `skill-sdk`: the contract every skill (bundled or user) can rely on — streaming handlers,
  `ctx.task_broker` async dispatch, `ctx.notify` proactive delivery, `available` gating,
  user_skills discovery of an agent skill.

### Modified Capabilities
<!-- none — existing specs (deployment, failure-reporting, llm-configuration, packaging,
     setup-gui, tts-backends, wake-word-default, ws-protocol) are untouched; local-only
     exhaustion UX is preserved as-is -->

## Impact

- Code: `vocascade/waterfall/router.py`, `vocascade/waterfall/stages/hermes.py` (deleted),
  `vocascade/skills/registry.py`, `vocascade/skills/context.py`,
  `vocascade/skills/base_skills/hermes.py`, `vocascade/config.py` (waterfall keys),
  `config.yaml.example`.
- Tests: `test_waterfall.py`, `test_hermes_stage.py`, `test_degradation.py`, `test_stop.py`,
  eval fixtures if they assert the `hermes` stage name.
- Unchanged: TTS conveyor (#169), `task_broker.py` / `hermes_run_client.py` /
  `delivery.py` internals, startup health probe and setup GUI (they probe the reference
  skill's `HERMES_BASE_URL` config, which remains), single-session WS, docs/protocol.md.
