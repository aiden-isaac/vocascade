# Contract: Skill SDK surface

**Status**: Pinned — frozen before user-skill code depends on it. This is the **stable surface that user skills depend on**: changing
it is a breaking change for `user_skills/`. Keep it small and additive.

## Registration

```python
from vocascade.skills import skill, SkillContext

@skill(
    name="tasks",                                  # unique; used in config + trace
    examples=["what are my tasks today",           # feed the medium-stage classifier
              "add a task to buy milk"],           #   prompt (capped per skill, OQ-5)
    keywords=["task", "todo", "remind me"],        # fast high-stage matching
)
async def handle(intent: str, entities: dict, ctx: SkillContext) -> str:
    tasks = await ctx.tools.todoist.get_today()
    return format_for_voice(tasks)                 # spoken text (string), or yield chunks
```

- The decorator registers the handler into the process-wide `SkillRegistry`.
- `name` MUST be unique; a duplicate name is a startup error.
- `examples` and `keywords` MAY be empty (a skill can rely solely on a custom
  `confidence` scorer, like smalltalk's fixed floor).
- A handler returns a `str` (full spoken text) **or** is an async generator
  yielding text chunks (streamed into TTS).
- An optional `confidence=callable` overrides scoring:
  `confidence(utterance: str) -> float`. Smalltalk returns `0.35` unconditionally.

## SkillContext (passed to every handler)

| Field | Type | Guarantee |
|-------|------|-----------|
| `tools` | `ToolBag` | Lazily-constructed integration clients; only configured providers are present. |
| `session` | `SessionState` | Read-only view of the current session (state, history pointer, ids). |
| `history` | `list[Turn]` | Recent voice turns (sliding window). |
| `config` | `UserConfig` | Resolved config for this skill and globals. |
| `emit_filler` | `async (category: str) -> None` | Lets the skill play its own filler/ack audio. |
| `local_llm` | `LocalLLM` | For smalltalk + optimistic openings only. MUST NOT be handed Hermes/skill tool schemas (FR-032). |

## Stage interface (for custom waterfall stages)

```python
from vocascade.waterfall.types import WaterfallStage, ConfidenceResult

class MyStage(WaterfallStage):
    name = "my_stage"
    async def evaluate(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        ...
```

- `ConfidenceResult(stage, confidence, skill_name=None, payload={})`.
- The router calls enabled stages in configured order; first result with
  `confidence >= threshold` wins; ties → earlier stage (FR-010, FR-011).
- A stage MUST be importable and unit-testable without the audio pipeline
  (FR-015) — this is what the eval harness (FR-120) exercises.

## Discovery & config

- Bundled skills live in `vocascade/skills/base_skills/`.
- User skills are auto-discovered from `user_skills/` at startup; an import
  error in one file isolates to that skill (logged + skipped), never aborting
  startup (FR-022).
- Per-skill config in `config.yaml`:

```yaml
skills:
  tasks:
    enabled: true
    provider: todoist
    filler: "Checking your tasks..."
waterfall:
  order: [stop, converse, high, medium, smalltalk, hermes]
  medium_threshold: 0.5
  smalltalk_confidence: 0.35
```

## Stability rules

- Additive changes (new optional `SkillContext` fields, new optional decorator
  kwargs) are non-breaking.
- Renaming/removing a `SkillContext` field, changing the handler signature, or
  changing the `confidence`/return protocol is breaking — version the SDK and
  note it here.
