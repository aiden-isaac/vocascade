"""
vocascade/waterfall/router.py — Confidence waterfall router and stage implementations.
"""

import logging
from typing import List, Dict, Any, Optional
from vocascade.waterfall.types import WaterfallStage, ConfidenceResult
from vocascade.skills.registry import registry
from vocascade.skills.context import SkillContext

logger = logging.getLogger("vocascade.waterfall.router")

class StopStage(WaterfallStage):
    """STOP/system stage (always first). Stubbed to 0.0 for Phase 3."""
    async def evaluate(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        return ConfidenceResult(stage=self.name, confidence=0.0)

class ConverseStage(WaterfallStage):
    """CONVERSE multi-turn claim stage. Stubbed to 0.0 for Phase 3."""
    async def evaluate(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        return ConfidenceResult(stage=self.name, confidence=0.0)

class HighStage(WaterfallStage):
    """HIGH confidence keyword/regex stage. Stubbed to 0.0 for Phase 3."""
    async def evaluate(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        return ConfidenceResult(stage=self.name, confidence=0.0)

class MediumStage(WaterfallStage):
    """MEDIUM confidence local-LLM classifier stage. Stubbed to 0.0 for Phase 3."""
    async def evaluate(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        return ConfidenceResult(stage=self.name, confidence=0.0)

class SmalltalkStage(WaterfallStage):
    """SMALLTALK floor fallback stage. Returns fixed 0.35 floor if smalltalk skill registered."""
    async def evaluate(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        skill_obj = registry.get_skill("smalltalk")
        if not self.enabled or not skill_obj:
            return ConfidenceResult(stage=self.name, confidence=0.0)
            
        conf = 0.35
        if skill_obj.confidence:
            try:
                # Custom confidence scorer is a callable: (utterance) -> float
                if callable(skill_obj.confidence):
                    conf = skill_obj.confidence(utterance)
                else:
                    conf = float(skill_obj.confidence)
            except Exception as e:
                logger.error(f"Error evaluating smalltalk custom confidence scorer: {e}")
                
        return ConfidenceResult(
            stage=self.name,
            confidence=conf,
            skill_name="smalltalk"
        )

class HermesStage(WaterfallStage):
    """HERMES always-async last stage. Acts as absolute fallback passthrough for Phase 3."""
    async def evaluate(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        if not self.enabled:
            return ConfidenceResult(stage=self.name, confidence=0.0)
        return ConfidenceResult(
            stage=self.name,
            confidence=1.0,
            skill_name="hermes"
        )


class WaterfallRouter:
    """
    Confidence waterfall router.
    Evaluates enabled stages in the order defined in config.yaml.
    The first stage that evaluates with confidence >= threshold wins.
    """
    def __init__(self, stages: List[WaterfallStage], thresholds: Dict[str, float]):
        self.stages = stages
        self.thresholds = thresholds

    async def resolve(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        logger.info(f"WaterfallRouter resolving: '{utterance}'")
        for stage in self.stages:
            if not stage.enabled:
                logger.debug(f"Stage '{stage.name}' is disabled. Skipping.")
                continue

            try:
                result = await stage.evaluate(utterance, ctx)
                threshold = stage.threshold
                logger.debug(f"Stage '{stage.name}' evaluated with confidence {result.confidence} (threshold {threshold})")

                if result.confidence >= threshold:
                    logger.info(f"WaterfallRouter: stage '{stage.name}' won (confidence: {result.confidence}, skill: {result.skill_name})")
                    return result
            except Exception as e:
                logger.error(f"Error evaluating stage '{stage.name}': {e}", exc_info=True)

        logger.warning("WaterfallRouter: no stage met its threshold. Falling back to hermes.")
        return ConfidenceResult(stage="hermes", confidence=1.0, skill_name="hermes")

    @classmethod
    def from_config(cls, config) -> "WaterfallRouter":
        """Factory method to construct WaterfallRouter and its stages from config."""
        stages = []
        thresholds = config.waterfall_thresholds
        stages_list = config.waterfall_stages

        # Mapping stage name -> Stage class
        stage_mapping = {
            "stop": StopStage,
            "converse": ConverseStage,
            "high": HighStage,
            "medium": MediumStage,
            "smalltalk": SmalltalkStage,
            "hermes": HermesStage,
        }

        for name in stages_list:
            if name not in stage_mapping:
                logger.warning(f"Unknown stage '{name}' in config. Skipping.")
                continue

            stage_cls = stage_mapping[name]
            enabled = config.skills_config.get(name, {}).get("enabled", True) if name in config.skills_config else True

            # Determine threshold
            if name == "high":
                threshold = thresholds.get("high", 0.95)
            elif name == "medium":
                threshold = thresholds.get("medium", 0.65)
            elif name == "smalltalk":
                # Fall back to low threshold or smalltalk_confidence (default 0.35)
                threshold = thresholds.get("low", 0.35)
            elif name == "hermes":
                # Absolute fallback: HERMES reports confidence 1.0, so a 0.0
                # threshold guarantees it always clears when reached last.
                threshold = 0.0
            else:
                # stop/converse are still stubs that report confidence 0.0. A 0.0
                # threshold would let them win with `0.0 >= 0.0` and short-circuit
                # the whole waterfall (skill_name=None → silent turn). Park them
                # above 1.0 so they never clear until implemented (US2/US5).
                threshold = 1.1

            stages.append(stage_cls(name=name, threshold=threshold, enabled=enabled))
            logger.info(f"Initialized stage '{name}' (threshold: {threshold}, enabled: {enabled})")

        return cls(stages=stages, thresholds=thresholds)
