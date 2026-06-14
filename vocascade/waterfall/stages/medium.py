"""
vocascade/waterfall/stages/medium.py — MEDIUM confidence stage (FR-013, OQ-5).

Classifies the utterance against registered skills using the local LLM, then
clamps the classifier's confidence into the configured medium band so a
poorly-calibrated model can neither leak into the HIGH band (starving keyword
skills) nor undercut the smalltalk floor. A "none"/unknown result, a missing
LLM, or any classifier failure yields confidence 0.0 (the stage is skipped,
never crashes).
"""

import logging
from typing import Optional, Tuple

from vocascade.waterfall.types import WaterfallStage, ConfidenceResult
from vocascade.waterfall.classifier import IntentClassifier

logger = logging.getLogger("vocascade.waterfall.stages.medium")


class MediumStage(WaterfallStage):
    """Local-LLM intent classifier stage."""

    def __init__(
        self,
        name: str = "medium",
        threshold: float = 0.65,
        enabled: bool = True,
        classifier: Optional[IntentClassifier] = None,
        llm=None,
        band: Tuple[float, float] = (0.5, 0.8),
    ):
        super().__init__(name=name, threshold=threshold, enabled=enabled)
        self.classifier = classifier or IntentClassifier()
        # Dedicated classifier LLM (may use a cheaper model than smalltalk's);
        # falls back to the per-invocation ctx.local_llm when not provided.
        self.llm = llm
        self.band_low, self.band_high = band

    async def evaluate(self, utterance: str, ctx) -> ConfidenceResult:
        llm = self.llm or getattr(ctx, "local_llm", None)
        if llm is None:
            logger.debug("MediumStage: no local LLM available; skipping.")
            return ConfidenceResult(stage=self.name, confidence=0.0)

        label, raw = await self.classifier.classify(utterance, llm)
        if label == "none":
            return ConfidenceResult(stage=self.name, confidence=0.0)

        clamped = max(self.band_low, min(self.band_high, raw))
        return ConfidenceResult(
            stage=self.name,
            confidence=clamped,
            skill_name=label,
            payload={"raw_confidence": raw},
        )
