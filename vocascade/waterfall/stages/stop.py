"""
vocascade/waterfall/stages/stop.py — STOP / SYSTEM stage (US5 / T231).

Always first in the waterfall. Catches two system intents before any skill or
the agent sees them:

  • **stop** — "stop", "cancel", "never mind" → cancel in-flight work and stay
    listening (FR-070/071). Caught here so a bare "stop" never dispatches a run.
  • **farewell** — "that will be all", "goodbye" → sign off and return to passive
    listening (FR-062). Caught here so a farewell never dispatches a run either.

The stage only *classifies*; the RouterStage performs the cancellation / teardown
(it owns the broker and pipeline handles).
"""

from vocascade.waterfall.types import WaterfallStage, ConfidenceResult
from vocascade.session.teardown import normalize, is_farewell

# Bare, unambiguous stop commands (exact normalized match, so "stop the timer"
# stays a skill command, not a cancel).
_STOP_PHRASES = frozenset({
    "stop", "stop it", "stop please", "please stop", "stop stop",
    "cancel", "cancel that", "never mind", "nevermind",
    "be quiet", "quiet", "shut up", "enough", "thats enough", "that's enough",
    "abort", "wait stop",
})


def is_stop(text: str) -> bool:
    return normalize(text) in _STOP_PHRASES


class StopStage(WaterfallStage):
    """Detects stop / farewell system intents (routes to the `stop` handler)."""

    async def evaluate(self, utterance: str, ctx) -> ConfidenceResult:
        if not self.enabled:
            return ConfidenceResult(stage=self.name, confidence=0.0)
        if is_stop(utterance):
            return ConfidenceResult(stage=self.name, confidence=1.0,
                                    skill_name="stop", payload={"action": "stop"})
        if is_farewell(utterance):
            return ConfidenceResult(stage=self.name, confidence=1.0,
                                    skill_name="stop", payload={"action": "farewell"})
        return ConfidenceResult(stage=self.name, confidence=0.0)
