"""
vocascade/waterfall/classifier.py — Medium-stage intent classifier (OQ-5, FR-013).

The classifier prompt is generated automatically at startup from the registered
skills' `examples` (SC-007 — contributors never hand-edit a prompt). Examples are
capped per-skill and in total to bound latency and token cost as the skill set
grows. The local LLM returns a skill label (or "none") plus a confidence; parsing
is tolerant and never raises — malformed output maps to no-match (FR/edge case:
"malformed or out-of-band output … never a crash").
"""

import re
import json
import logging
from typing import Callable, List, Optional, Set, Tuple

from vocascade.skills.registry import registry, Skill

logger = logging.getLogger("vocascade.waterfall.classifier")

_SYSTEM_TEMPLATE = """You are an intent classifier for a voice assistant.
Pick the ONE skill that should handle the user's utterance, or "none" if no skill fits.
Reply with ONLY a compact JSON object and nothing else:
{{"skill": "<skill_name_or_none>", "confidence": <number between 0 and 1>}}

Available skills (name: example phrases):
{skill_block}"""


class IntentClassifier:
    """Builds the classification prompt from skill examples and scores utterances."""

    def __init__(
        self,
        skills_provider: Optional[Callable[[], List[Skill]]] = None,
        max_examples_per_skill: int = 5,
        max_total_examples: int = 40,
    ):
        self._skills_provider = skills_provider or registry.get_all_skills
        self.max_examples_per_skill = max_examples_per_skill
        self.max_total_examples = max_total_examples
        self._prompt: Optional[str] = None
        self._skill_names: Set[str] = set()

    # --- prompt generation -------------------------------------------------

    def build_prompt(self) -> str:
        """(Re)generate the classifier prompt from the current registry (SC-007)."""
        lines: List[str] = []
        names: Set[str] = set()
        total = 0
        for skill in self._skills_provider():
            # Only skills that offer example phrases participate in classification.
            if not skill.examples:
                continue
            remaining = self.max_total_examples - total
            if remaining <= 0:
                break
            examples = skill.examples[: self.max_examples_per_skill][:remaining]
            if not examples:
                continue
            total += len(examples)
            names.add(skill.name)
            lines.append(f"- {skill.name}: " + "; ".join(examples))

        self._skill_names = names
        self._prompt = _SYSTEM_TEMPLATE.format(
            skill_block="\n".join(lines) if lines else "(no classifiable skills)"
        )
        return self._prompt

    @property
    def prompt(self) -> str:
        if self._prompt is None:
            self.build_prompt()
        return self._prompt

    @property
    def skill_names(self) -> Set[str]:
        if self._prompt is None:
            self.build_prompt()
        return self._skill_names

    # --- classification ----------------------------------------------------

    async def classify(self, utterance: str, llm) -> Tuple[str, float]:
        """Return ``(skill_name | "none", raw_confidence)``. Never raises."""
        if not self.skill_names:
            return ("none", 0.0)

        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": utterance},
        ]
        try:
            raw = await llm.chat(messages, temperature=0.0, max_tokens=40)
        except Exception as exc:  # network / model failure → skip the stage
            logger.error("Medium-stage classifier LLM call failed: %s", exc)
            return ("none", 0.0)

        return self._parse(raw)

    def _parse(self, raw: str) -> Tuple[str, float]:
        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            obj = json.loads(match.group(0) if match else raw)
            label = str(obj.get("skill", "none")).strip()
            conf = float(obj.get("confidence", 0.0))
        except (ValueError, TypeError, AttributeError):
            logger.warning("Classifier returned unparseable output: %r", raw)
            return ("none", 0.0)

        # Reject "none", unknown, or hallucinated labels.
        if label not in self._skill_names:
            return ("none", 0.0)
        # Guard NaN / out-of-range before the stage clamps into its band.
        if conf != conf or conf < 0.0:
            conf = 0.0
        elif conf > 1.0:
            conf = 1.0
        return (label, conf)
