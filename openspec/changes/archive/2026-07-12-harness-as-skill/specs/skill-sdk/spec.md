# skill-sdk — contract for bundled and user skills

## ADDED Requirements

### Requirement: Streaming skill handlers
A skill handler MAY be an async generator; each yielded string SHALL be spoken as it arrives
via the shared TTS conveyor. This is the mechanism an agent skill uses to stream a live reply
sentence-by-sentence.

#### Scenario: Agent skill streams a reply
- **WHEN** the agent skill yields sentences as its backend's deltas arrive
- **THEN** TTS begins speaking the first sentence before the reply is complete

### Requirement: Async dispatch via ctx.task_broker
`SkillContext.task_broker` SHALL be documented, stable SDK surface: the app-level async-run
broker available to ANY skill (not only the bundled agent skill), `None` when no backend is
configured. Skills MUST handle `None` gracefully.

#### Scenario: Skill dispatches a long-running task
- **WHEN** a skill calls `ctx.task_broker.dispatch(prompt, session_id=...)`
- **THEN** the run is dispatched asynchronously and a completion arriving after the turn is
  delivered proactively by the broker

#### Scenario: No broker configured
- **WHEN** a skill runs with `ctx.task_broker is None`
- **THEN** the skill degrades with a spoken notice instead of crashing

### Requirement: Proactive delivery via ctx.notify
`SkillContext.notify` SHALL be documented, stable SDK surface: any skill can schedule speech
for the next idle moment (idle-gated FIFO), including from background tasks it spawns.

#### Scenario: Late result spoken proactively
- **WHEN** a skill's background work completes while the user is mid-conversation
- **THEN** the notification is queued and spoken at the next idle moment, never over live speech

### Requirement: Skill availability gate
`@skill` SHALL accept an optional zero-arg `available` callable. A skill whose gate returns
falsy (or raises, logged as a warning) SHALL be treated as unavailable for role claims such as
`waterfall.agent_skill`; a missing gate means always available. A raising gate MUST NOT crash
startup.

#### Scenario: Reference skill gates on its endpoint
- **WHEN** `base_skills/hermes.py` registers with `available` checking `HERMES_BASE_URL`
- **THEN** the skill is unavailable for the agent role exactly when the endpoint is unset

#### Scenario: Broken user-skill gate
- **WHEN** a user skill's `available` raises
- **THEN** startup continues, the skill is treated as unavailable, and a warning names it

### Requirement: Reference agent skill is self-contained
`vocascade/skills/base_skills/hermes.py` SHALL contain the complete reference agent-skill
implementation (dispatch, live-sink streaming through the speech chunker, degraded path) with
no imports from `vocascade/waterfall/`; it is the documented template for third-party agents.

#### Scenario: Stranger copies the template
- **WHEN** a user copies `base_skills/hermes.py` to `user_skills/my_agent.py` and swaps the
  backend calls
- **THEN** no waterfall-package import is needed for the copy to stream, dispatch, and degrade
