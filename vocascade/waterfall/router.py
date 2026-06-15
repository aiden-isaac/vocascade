"""
vocascade/waterfall/router.py — Confidence waterfall router and stage implementations.
"""

import logging
from typing import List, Dict
from vocascade.waterfall.types import WaterfallStage, ConfidenceResult
from vocascade.waterfall.classifier import IntentClassifier
from vocascade.waterfall.stages.high import HighStage
from vocascade.waterfall.stages.medium import MediumStage
from vocascade.waterfall.stages.hermes import HermesStage
from vocascade.waterfall.stages.stop import StopStage
from vocascade.waterfall.stages.converse import ConverseStage
from vocascade.skills.registry import registry
from vocascade.skills.context import SkillContext

logger = logging.getLogger("vocascade.waterfall.router")

# STOP/CONVERSE/HIGH/MEDIUM/HERMES all live in stages/ now (US2/US3/US5).
# SmalltalkStage stays here — it is the local-LLM floor (US1).

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

# HermesStage (the always-async last stage) lives in stages/hermes.py (US3).


# Stages constructed generically (HIGH/MEDIUM need injected deps, handled separately).
_STUB_STAGE_CLASSES = {
    "stop": StopStage,
    "converse": ConverseStage,
    "smalltalk": SmalltalkStage,
    "hermes": HermesStage,
}


def _threshold_for(name: str, thresholds: Dict[str, float]) -> float:
    """Resolve a stage's win threshold from config (FR-014)."""
    if name == "high":
        return thresholds.get("high", 0.95)
    if name == "medium":
        return thresholds.get("medium", 0.65)
    if name == "smalltalk":
        return thresholds.get("low", 0.35)
    if name == "hermes":
        # HERMES reports confidence 1.0; a 0.0 threshold guarantees it always
        # clears as the last-resort fallback.
        return 0.0
    if name in ("stop", "converse"):
        # Real system stages (US5): report 1.0 on a deterministic match, 0.0
        # otherwise. A 0.5 bar lets a match win without a no-match short-circuit.
        return 0.5
    return 1.1   # unknown stage — never wins


def _ordered_stage_names(names: List[str]) -> List[str]:
    """Honor FR-011: STOP first, HERMES last; keep the rest in configured order."""
    ordered = list(names)
    changed = False
    if "stop" in ordered and ordered[0] != "stop":
        ordered.remove("stop")
        ordered.insert(0, "stop")
        changed = True
    if "hermes" in ordered and ordered[-1] != "hermes":
        ordered.remove("hermes")
        ordered.append("hermes")
        changed = True
    if changed:
        logger.warning("Reordered waterfall stages to honor STOP-first/HERMES-last (FR-011): %s", ordered)
    return ordered


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
        """Construct the router and its stages from config (FR-011/FR-014)."""
        thresholds = config.waterfall_thresholds
        stages_list = _ordered_stage_names(config.waterfall_stages)

        # Medium-stage deps (OQ-5): a dedicated classifier LLM (optionally a
        # cheaper model than smalltalk's) and a prompt auto-generated once at
        # startup from the registered skills' examples (SC-007).
        classifier_llm = None
        if getattr(config, "llm_base_url", None):
            from vocascade.gateway.local_llm import LocalLLM
            classifier_llm = LocalLLM(
                base_url=config.llm_base_url,
                api_key=config.llm_api_key,
                model=getattr(config, "classifier_model", None) or config.llm_model,
                timeout=getattr(config, "classifier_timeout_seconds", 6.0),
            )
        classifier = IntentClassifier(
            max_examples_per_skill=getattr(config, "classifier_max_examples", 5),
        )
        classifier.build_prompt()
        band = (
            getattr(config, "medium_band_low", 0.5),
            getattr(config, "medium_band_high", 0.8),
        )

        stages: List[WaterfallStage] = []
        for name in stages_list:
            enabled = config.skills_config.get(name, {}).get("enabled", True)
            threshold = _threshold_for(name, thresholds)

            if name == "high":
                stage = HighStage(name=name, threshold=threshold, enabled=enabled)
            elif name == "medium":
                stage = MediumStage(
                    name=name, threshold=threshold, enabled=enabled,
                    classifier=classifier, llm=classifier_llm, band=band,
                )
            else:
                stage_cls = _STUB_STAGE_CLASSES.get(name)
                if stage_cls is None:
                    logger.warning(f"Unknown stage '{name}' in config. Skipping.")
                    continue
                stage = stage_cls(name=name, threshold=threshold, enabled=enabled)

            stages.append(stage)
            logger.info(f"Initialized stage '{name}' (threshold: {threshold}, enabled: {enabled})")

        return cls(stages=stages, thresholds=thresholds)
