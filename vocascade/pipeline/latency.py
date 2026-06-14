"""
vocascade/pipeline/latency.py — Latency masking (US4).

A layer distinct from routing (FR-040) that decides what to play *before* a
winning skill produces output, so the loop never has dead air while it fetches
or thinks:

  • HIGH  → nothing; the answer is already instant (FR-041).
  • MEDIUM→ a tool-appropriate filler clip, then the skill streams (FR-042).
  • HERMES→ a query-appropriate opening ("Let me check on that."), then the run's
           streamed output continues the utterance (FR-043).

Pre-rendered clips (KEEP `filler_engine.py`) are instant; an opening with no
clip falls back to a short, voice-optimized line — generated optimistically by
the local LLM (T227) with a templated fallback so it never blocks or crashes.
"""

import re
import random
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("vocascade.pipeline.latency")

EmitClip = Callable[[bytes], Awaitable[None]]
EmitText = Callable[[str], Awaitable[None]]

_GENERIC_OPENINGS = [
    "Let me look into that.",
    "Let me check on that.",
    "One moment.",
]

_OPENING_PROMPT = (
    "You are a voice assistant. The user's request needs a lookup or action that "
    "takes a few seconds. Reply with ONE very short, natural sentence that says "
    "you're on it — do NOT answer the request. Under 8 words. No quotes."
)


@dataclass
class FillerDecision:
    kind: str            # "none" | "clip" | "opening"
    category: str = ""   # filler-engine category for "clip"/"opening"


def optimize_for_voice(text: str, max_chars: int = 240) -> str:
    """Strip markdown/whitespace and cap to a sentence boundary for clean TTS (T227)."""
    if not text:
        return ""
    text = re.sub(r"[*_`#>\[\]]+", "", text)     # markdown noise
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        bounds = list(re.finditer(r"[.!?]", cut))
        text = cut[: bounds[-1].end()] if bounds else cut.rstrip() + "…"
    return text


class FillerPolicy:
    """Maps a winning stage (+ that skill's config) to a masking decision.
    Pure and side-effect free — independently testable (FR-040/FR-015)."""

    def decide(self, stage: str, skill_config: Optional[dict] = None) -> FillerDecision:
        cfg = skill_config or {}
        # An explicit empty/"none" filler disables masking for that skill.
        configured = cfg.get("filler")
        if configured in ("", "none", False):
            return FillerDecision("none")

        if stage == "high":
            return FillerDecision("none")                       # FR-041
        if stage == "medium":
            return FillerDecision("clip", category=configured or "thinking")   # FR-042
        if stage == "hermes":
            return FillerDecision("opening", category=configured or "working")  # FR-043
        return FillerDecision("none")                           # smalltalk / stop / converse


class OptimisticOpening:
    """Generates a short 'working on it' opening (T227), LLM-first with a
    deterministic templated fallback."""

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    async def generate(self, utterance: str, local_llm) -> str:
        if self.use_llm and local_llm is not None:
            try:
                text = await local_llm.chat(
                    [{"role": "system", "content": _OPENING_PROMPT},
                     {"role": "user", "content": utterance}],
                    temperature=0.5, max_tokens=24,
                )
                text = optimize_for_voice(text)
                if text:
                    return text
            except Exception as exc:
                logger.warning("Opening generation failed (%s); using a generic line", exc)
        return random.choice(_GENERIC_OPENINGS)


class LatencyMasker:
    """Applies a FillerDecision by emitting an instant clip or a spoken opening."""

    def __init__(self, filler_engine=None, policy: Optional[FillerPolicy] = None,
                 opening: Optional[OptimisticOpening] = None):
        self.filler_engine = filler_engine
        self.policy = policy or FillerPolicy()
        self.opening = opening or OptimisticOpening()

    def _clip(self, category: str) -> Optional[bytes]:
        if self.filler_engine is None:
            return None
        return self.filler_engine.get_filler(category)

    async def mask(self, *, stage: str, skill_config: Optional[dict], utterance: str,
                   local_llm, emit_clip: EmitClip, emit_text: EmitText) -> FillerDecision:
        decision = self.policy.decide(stage, skill_config)
        if decision.kind == "none":
            return decision

        if decision.kind == "clip":
            pcm = self._clip(decision.category)
            if pcm:
                await emit_clip(pcm)
            # No clip available → skip rather than invent dead air.
        elif decision.kind == "opening":
            pcm = self._clip(decision.category)
            if pcm:
                await emit_clip(pcm)                      # instant clip preferred
            else:
                text = await self.opening.generate(utterance, local_llm)
                if text:
                    await emit_text(text)
        return decision
