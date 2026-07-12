# harness-as-skill — Tasks

## 1. Skill SDK

- [x] 1.1 Add optional zero-arg `available` callable to `Skill` dataclass and `@skill` /
      `SkillRegistry.register` (default None = always available); registry helper
      `is_available(name)` that returns False and logs a warning if the gate raises
- [x] 1.2 Update `SkillContext` docstrings: `task_broker` is stable SDK surface for any skill
      (None when no backend configured); document `notify` alongside
- [x] 1.3 Unit tests: `available` default, falsy gate, raising gate (no crash, warning)

## 2. Reference agent skill

- [x] 2.1 Move `stream_hermes_reply` + chunker re-exports from `waterfall/stages/hermes.py`
      into `skills/base_skills/hermes.py`; no `vocascade.waterfall` imports remain in the skill
- [x] 2.2 Register hermes skill with `available=lambda: bool(os.getenv("HERMES_BASE_URL", "").strip())`
- [x] 2.3 Update `tests/unit/test_hermes_stage.py` imports to the new location (rename file to
      `test_hermes_skill.py`)

## 3. Generic agent stage

- [x] 3.1 Add `AgentStage` (confidence 1.0 → `skill_name = agent_skill` when registered, else
      0.0) in `waterfall/stages/`; delete `waterfall/stages/hermes.py`
- [x] 3.2 Config: parse `waterfall.agent_skill` (default `hermes`) in `config.py`; update
      `config.yaml.example` stage list to `agent` + `agent_skill` key
- [x] 3.3 Router `from_config`: alias legacy `hermes` stage name → `agent` with deprecation
      warning; build agent stage only when claimed skill is registered AND available (log the
      drop reason); remove the `hermes_base_url` special case
- [x] 3.4 Router generics: `_threshold_for("agent") → 0.0`, `_ordered_stage_names` enforces
      AGENT-last, `resolve()` exhaustion falls back to the agent stage instance (no name match)
- [x] 3.5 Update SmalltalkStage gate comments/logs that reference "Hermes" to "the agent"

## 4. Tests and eval

- [x] 4.1 Update `test_waterfall.py` (from_config, ordering, exhaustion) and
      `test_degradation.py` / `test_stop.py` for the `agent` stage name
- [x] 4.2 New tests: third-party skill claims the role via `agent_skill`; unregistered
      `agent_skill` → stage not built, local-only exhaustion; legacy `hermes` alias warning
- [x] 4.3 Update `eval/fixtures.jsonl` / route-harness expectations from `hermes` → `agent`
- [x] 4.4 Full suite green: `.venv/bin/python -m pytest tests/ -q` (333 passed, 7 skipped)

## 5. Docs

- [x] 5.1 README "Bring your own agent" section: drop `my_agent.py` in `user_skills/`, set
      `waterfall.agent_skill`, point at `base_skills/hermes.py` as the template
- [x] 5.2 Update CLAUDE/AGENTS instructions: waterfall diagram `HERMES` → `AGENT`, gotchas
      entry for `agent_skill`
