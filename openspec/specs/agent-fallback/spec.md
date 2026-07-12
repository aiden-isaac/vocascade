# agent-fallback Specification

## Purpose
The waterfall's generic last-resort stage: how a skill claims the agent role
via `waterfall.agent_skill`, the ordering/exhaustion rules, and the
availability-driven local-only behavior. The waterfall core names no
particular agent.
## Requirements
### Requirement: Generic agent stage claims the waterfall fallback role
The waterfall SHALL provide a generic `agent` stage that routes to the skill named by
`waterfall.agent_skill` in `config.yaml` (default `hermes`). The waterfall core MUST NOT
reference any specific agent by name.

#### Scenario: Configured agent skill wins on fallthrough
- **WHEN** `waterfall.agent_skill: hermes` is set, the hermes skill is registered and available,
  and no earlier stage clears its threshold
- **THEN** the agent stage wins with confidence 1.0 and routing resolves to the `hermes` skill

#### Scenario: Third-party skill claims the role
- **WHEN** a user drops `my_agent.py` into `user_skills/` registering skill `my_agent` and sets
  `waterfall.agent_skill: my_agent`
- **THEN** fallthrough utterances route to `my_agent` with no code changes to the core

### Requirement: Agent stage ordering and exhaustion fallback are generic
The router SHALL enforce STOP-first/AGENT-last ordering and SHALL use the agent stage instance
(not a hardcoded name) as the exhaustion fallback when no stage clears its threshold.

#### Scenario: Misordered config is corrected
- **WHEN** `config.yaml` lists the `agent` stage before `smalltalk`
- **THEN** the router reorders it last and logs a warning

#### Scenario: Exhaustion falls back to the agent stage
- **WHEN** every stage evaluates below its threshold and an agent stage is built
- **THEN** the router returns the agent stage's skill as the winner, traced as stage `agent`

### Requirement: Agent stage is built only when its skill is usable
The router SHALL build the agent stage only when the claimed skill is registered and its
`available` gate (if any) returns truthy. When the stage is not built, waterfall exhaustion
SHALL keep the existing local-only behavior (spoken can't-help notice, never silence).

#### Scenario: Reference skill without a backend
- **WHEN** `waterfall.agent_skill: hermes` and `HERMES_BASE_URL` is unset
- **THEN** the agent stage is dropped with a log line and exhaustion speaks the can't-help notice

#### Scenario: agent_skill names an unregistered skill
- **WHEN** `waterfall.agent_skill: nope` and no skill `nope` is registered
- **THEN** startup logs the problem, the stage is not built, and local-only behavior applies

### Requirement: Legacy `hermes` stage name is aliased
The router SHALL treat the legacy stage name `hermes` in `waterfall.stages` as `agent` with
`agent_skill` defaulting to `hermes`, logging a deprecation warning.

#### Scenario: Pre-existing config keeps working
- **WHEN** an existing `config.yaml` lists `- hermes` in the stage order
- **THEN** the server starts, behaves identically to an `agent` stage claiming `hermes`, and
  logs a deprecation warning naming the new keys
