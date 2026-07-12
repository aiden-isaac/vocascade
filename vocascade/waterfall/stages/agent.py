"""
vocascade/waterfall/stages/agent.py — generic AGENT always-async last stage.

The waterfall's last resort: anything no local skill claims is handed to
whatever skill `waterfall.agent_skill` names in config.yaml (default: the
bundled `hermes` reference skill). The stage only routes — dispatch,
streaming, and proactive delivery are the claiming skill's business via
`ctx.task_broker` / `ctx.notify`. The waterfall core names no particular
agent.
"""

import logging

from vocascade.waterfall.types import WaterfallStage, ConfidenceResult
from vocascade.skills.registry import registry

logger = logging.getLogger("vocascade.waterfall.stages.agent")


class AgentStage(WaterfallStage):
    """Absolute fallback: reports confidence 1.0 so it always clears its (0.0)
    threshold when the waterfall reaches it. Routing resolves to the skill
    named by `waterfall.agent_skill`, whose streaming handler drives the
    actual dispatch."""

    def __init__(self, name: str = "agent", threshold: float = 0.0,
                 enabled: bool = True, agent_skill: str = "hermes"):
        super().__init__(name=name, threshold=threshold, enabled=enabled)
        self.agent_skill = agent_skill

    async def evaluate(self, utterance: str, ctx) -> ConfidenceResult:
        if not self.enabled or registry.get_skill(self.agent_skill) is None:
            return ConfidenceResult(stage=self.name, confidence=0.0)
        return ConfidenceResult(stage=self.name, confidence=1.0, skill_name=self.agent_skill)
