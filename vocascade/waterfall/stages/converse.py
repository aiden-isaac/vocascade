"""
vocascade/waterfall/stages/converse.py — CONVERSE multi-turn stage (US5 / T232).

Second in the waterfall (after STOP). When a skill has asked a follow-up
question it leaves a `ConverseClaim` on the session; the next utterance is then
routed to that claim's `resume` ahead of the normal waterfall (FR-080). An
expired claim is released and routing falls through normally (FR-081); STOP and
completion release it elsewhere.
"""

import time
import logging

from vocascade.waterfall.types import WaterfallStage, ConfidenceResult

logger = logging.getLogger("vocascade.waterfall.stages.converse")


class ConverseStage(WaterfallStage):
    """Claims the next utterance for a skill awaiting a follow-up reply."""

    async def evaluate(self, utterance: str, ctx) -> ConfidenceResult:
        session = getattr(ctx, "session", None)
        claim = getattr(session, "converse_claim", None) if session else None
        if not self.enabled or claim is None:
            return ConfidenceResult(stage=self.name, confidence=0.0)

        if claim.expires_at and time.time() > claim.expires_at:
            logger.info("Converse claim for '%s' expired; releasing", claim.skill_name)
            session.converse_claim = None
            return ConfidenceResult(stage=self.name, confidence=0.0)

        return ConfidenceResult(
            stage=self.name,
            confidence=1.0,
            skill_name=claim.skill_name,
            payload={"converse": True},
        )
