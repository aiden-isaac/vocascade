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

# Smalltalk routing gate (FR-033): a one-word local-LLM decision that keeps the
# smalltalk floor from swallowing utterances that actually need the agent. Kept
# deliberately tight so it never *answers* — it only routes.
_GATE_SYSTEM_PROMPT = """You route a user's message in a voice assistant.
Reply with ONE word, exactly SMALLTALK or AGENT.

SMALLTALK = greetings, chit-chat, opinions, persona/identity questions, or
general-knowledge questions you can answer directly from your own knowledge.
AGENT = anything needing the user's personal data (tasks, calendar, email, notes,
messages, files), real-time or external information (weather, news, prices, the
web, a server's status), device/home control, or taking an action (send,
schedule, remind, add, buy, check something).

Reply with ONLY the single word SMALLTALK or AGENT."""


class SmalltalkStage(WaterfallStage):
    """
    SMALLTALK floor fallback stage (FR-030). Reports a fixed low confidence so it
    wins only when nothing above it scored higher — *and*, when a local LLM is
    available, a content-aware gate (FR-033) makes it abstain for utterances that
    need the agent, so those fall through to the Hermes stage below it instead of
    being answered (badly) from general knowledge.
    """

    def __init__(self, name: str = "smalltalk", threshold: float = 0.35,
                 enabled: bool = True, llm=None, gate: bool = True):
        super().__init__(name=name, threshold=threshold, enabled=enabled)
        # Dedicated short-timeout gate LLM (shared with the medium classifier);
        # falls back to the per-invocation ctx.local_llm when not injected.
        self.llm = llm
        self.gate = gate

    async def evaluate(self, utterance: str, ctx: SkillContext) -> ConfidenceResult:
        skill_obj = registry.get_skill("smalltalk")
        if not self.enabled or not skill_obj:
            return ConfidenceResult(stage=self.name, confidence=0.0)

        conf = self._floor(skill_obj, utterance)

        # Content-aware gate: claim only genuine conversation; let data / tool /
        # real-time / action requests drop through to the Hermes fallback.
        llm = self.llm or getattr(ctx, "local_llm", None)
        if self.gate and llm is not None and conf > 0.0:
            try:
                if await self._needs_agent(utterance, llm):
                    logger.info("Smalltalk gate: '%s' needs the agent — abstaining to Hermes.", utterance)
                    return ConfidenceResult(stage=self.name, confidence=0.0)
            except Exception as e:
                # A gate failure must never starve smalltalk — answer locally.
                logger.warning("Smalltalk gate failed (%s); answering locally.", e)

        return ConfidenceResult(stage=self.name, confidence=conf, skill_name="smalltalk")

    def _floor(self, skill_obj, utterance: str) -> float:
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
        return conf

    async def _needs_agent(self, utterance: str, llm) -> bool:
        messages = [
            {"role": "system", "content": _GATE_SYSTEM_PROMPT},
            {"role": "user", "content": utterance},
        ]
        raw = await llm.chat(messages, temperature=0.0, max_tokens=3)
        return "AGENT" in (raw or "").upper()

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

    async def resolve(self, utterance: str, ctx: SkillContext, trace: list = None) -> ConfidenceResult:
        """Resolve the winning stage. If ``trace`` is provided, append a per-stage
        record to it (used by the eval harness, US9 — the live path passes none)."""
        logger.info(f"WaterfallRouter resolving: '{utterance}'")
        for stage in self.stages:
            if not stage.enabled:
                logger.debug(f"Stage '{stage.name}' is disabled. Skipping.")
                if trace is not None:
                    trace.append({"stage": stage.name, "confidence": None,
                                  "threshold": stage.threshold, "won": False, "skipped": True})
                continue

            try:
                result = await stage.evaluate(utterance, ctx)
                threshold = stage.threshold
                won = result.confidence >= threshold
                logger.debug(f"Stage '{stage.name}' evaluated with confidence {result.confidence} (threshold {threshold})")
                if trace is not None:
                    trace.append({"stage": stage.name, "confidence": result.confidence,
                                  "threshold": threshold, "won": won, "skill": result.skill_name})

                if won:
                    logger.info(f"WaterfallRouter: stage '{stage.name}' won (confidence: {result.confidence}, skill: {result.skill_name})")
                    return result
            except Exception as e:
                logger.error(f"Error evaluating stage '{stage.name}': {e}", exc_info=True)
                if trace is not None:
                    trace.append({"stage": stage.name, "error": str(e), "won": False})

        logger.warning("WaterfallRouter: no stage met its threshold. Falling back to hermes.")
        if trace is not None:
            trace.append({"stage": "hermes", "confidence": 1.0, "threshold": 0.0,
                          "won": True, "skill": "hermes", "fallback": True})
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
            elif name == "smalltalk":
                # The gate reuses the short-timeout classifier LLM (FR-033); a
                # per-skill `gate: false` reverts to the plain floor.
                gate = config.skills_config.get("smalltalk", {}).get("gate", True)
                stage = SmalltalkStage(
                    name=name, threshold=threshold, enabled=enabled,
                    llm=classifier_llm, gate=gate,
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
