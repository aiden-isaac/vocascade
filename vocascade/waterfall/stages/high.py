"""
vocascade/waterfall/stages/high.py — HIGH confidence stage (FR-012).

Fast, deterministic keyword/pattern matching over the registered skills'
`keywords`. No I/O, no LLM — matching a handful of pre-compiled word-boundary
regexes against a short utterance is microseconds, well inside the
"imperceptible" budget (T219: <5ms). A keyword hit returns a high confidence
that clears the configured HIGH threshold so the waterfall short-circuits here.
"""

import re
import logging
from functools import lru_cache
from typing import Callable, List, Optional

from vocascade.waterfall.types import WaterfallStage, ConfidenceResult
from vocascade.skills.registry import registry, Skill

logger = logging.getLogger("vocascade.waterfall.stages.high")


@lru_cache(maxsize=1024)
def _compile_keyword(keyword: str) -> re.Pattern:
    """Word-boundary, case-insensitive matcher for a keyword/phrase (cached)."""
    return re.compile(r"\b" + re.escape(keyword.strip()) + r"\b", re.IGNORECASE)


class HighStage(WaterfallStage):
    """Keyword/pattern stage. Wins (FR-012) when a skill keyword matches."""

    def __init__(
        self,
        name: str = "high",
        threshold: float = 0.95,
        enabled: bool = True,
        match_confidence: float = 1.0,
        skills_provider: Optional[Callable[[], List[Skill]]] = None,
    ):
        super().__init__(name=name, threshold=threshold, enabled=enabled)
        # Confidence reported on a keyword hit; defaults to 1.0 so it clears the
        # HIGH threshold (config-overridable) decisively.
        self.match_confidence = match_confidence
        # Read the live registry by default so skills registered after the stage
        # is constructed (e.g. in tests) are still matched.
        self._skills_provider = skills_provider or registry.get_all_skills

    async def evaluate(self, utterance: str, ctx) -> ConfidenceResult:
        # Most specific (longest) matching keyword wins; ties resolve to the
        # earlier-registered skill (registry insertion order), keeping the result
        # deterministic (FR-010 spirit, within a stage).
        best_len = -1
        best_skill: Optional[str] = None
        best_keyword: Optional[str] = None
        for skill in self._skills_provider():
            for kw in skill.keywords:
                if not kw:
                    continue
                if _compile_keyword(kw).search(utterance):
                    if len(kw) > best_len:
                        best_len = len(kw)
                        best_skill = skill.name
                        best_keyword = kw

        if best_skill is None:
            return ConfidenceResult(stage=self.name, confidence=0.0)

        return ConfidenceResult(
            stage=self.name,
            confidence=self.match_confidence,
            skill_name=best_skill,
            payload={"matched_keyword": best_keyword},
        )
