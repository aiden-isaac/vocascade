"""
vocascade/pipeline/latency.py — Latency masking (US4).

A layer distinct from routing (FR-040) so the loop never sits in dead air while
it fetches or thinks:

  • HIGH  → nothing; the answer is already instant (FR-041).
  • MEDIUM→ a spoken filler before the result, then the skill streams (FR-042).
  • HERMES→ a query-appropriate opening, then the run's streamed output continues
           the utterance (FR-043).

Fillers are generated **dynamically and spoken via TTS** — never pre-rendered
clips (those are reserved for the wakeword acknowledge). Content is configurable
(`pool` / `llm` / `hybrid`, FR-047), and while a slow stage keeps producing
nothing the masker emits **progressive follow-up fillers** at a configurable,
optionally backing-off interval until output arrives (FR-045). The local-LLM
prompt is tightly constrained so a filler never answers the request, asks a
question, or starts a conversation.
"""

import re
import random
import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("vocascade.pipeline.latency")

EmitText = Callable[[str], Awaitable[None]]

# Spoken-fresh phrase pools (TTS'd live — not pre-rendered clips).
_POOL_OPENINGS = [
    "Let me look into that.",
    "Let me check on that.",
    "One moment.",
    "On it.",
]
_POOL_FOLLOWUPS = [
    "Still working on it.",
    "This is taking a moment.",
    "Almost there.",
    "Hang tight, still going.",
]

_OPENING_PROMPT = (
    "You are a voice assistant. The user's request needs a lookup or action that "
    "takes a few seconds. Reply with ONE short, natural spoken sentence (under 8 "
    "words) that says you're on it. Rules: do NOT answer or attempt the request; "
    "do NOT ask a question or start a conversation; no 'loading' wording; no quotes."
)
_FOLLOWUP_PROMPT = (
    "You are a voice assistant still working on the user's request. Reply with ONE "
    "short, natural spoken sentence (under 8 words) reassuring them you're still on "
    "it. Rules: do NOT answer or attempt the request; do NOT ask a question or start "
    "a conversation; no 'loading' wording; no quotes."
)


@dataclass
class FillerDecision:
    kind: str            # "none" | "opening"
    category: str = ""


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


class FillerProvider:
    """Produces spoken filler lines. `mode` selects the source (FR-047):
    ``pool`` (built-in phrases), ``llm`` (local-LLM each line), or ``hybrid``
    (LLM opening for context, pooled follow-ups)."""

    def __init__(self, mode: str = "hybrid"):
        self.mode = mode if mode in ("pool", "llm", "hybrid") else "hybrid"

    async def opening(self, utterance: str, local_llm) -> str:
        if self.mode in ("llm", "hybrid") and local_llm is not None:
            text = await self._llm_line(_OPENING_PROMPT, utterance, local_llm)
            if text:
                return text
        return random.choice(_POOL_OPENINGS)

    async def followup(self, utterance: str, local_llm, idx: int) -> str:
        if self.mode == "llm" and local_llm is not None:
            text = await self._llm_line(_FOLLOWUP_PROMPT, utterance, local_llm)
            if text:
                return text
        # pool / hybrid → escalate through the pool, clamping at the last line.
        return _POOL_FOLLOWUPS[min(idx, len(_POOL_FOLLOWUPS) - 1)]

    async def _llm_line(self, prompt: str, utterance: str, local_llm) -> str:
        try:
            text = await local_llm.chat(
                [{"role": "system", "content": prompt},
                 {"role": "user", "content": utterance}],
                temperature=0.5, max_tokens=24,
            )
            return optimize_for_voice(text)
        except Exception as exc:
            logger.warning("Filler generation failed (%s); falling back to a pooled line", exc)
            return ""


class FillerPolicy:
    """Maps a winning stage (+ that skill's config) to a masking decision.
    Pure and side-effect free — independently testable (FR-040/FR-015)."""

    def decide(self, stage: str, skill_config: Optional[dict] = None) -> FillerDecision:
        cfg = skill_config or {}
        if cfg.get("filler") in ("", "none", False):     # explicit opt-out per skill
            return FillerDecision("none")
        if stage == "high":
            return FillerDecision("none")                 # FR-041
        if stage in ("medium", "hermes"):
            return FillerDecision("opening", category=cfg.get("filler") or stage)  # FR-042/043
        return FillerDecision("none")                     # smalltalk / stop / converse


class LatencyMasker:
    """Emits the opening filler and, for streamed skills, progressive follow-ups."""

    def __init__(self, filler_engine=None, provider: Optional[FillerProvider] = None,
                 policy: Optional[FillerPolicy] = None, *,
                 interval: float = 3.0, backoff: bool = True, max_fillers: int = 3):
        # filler_engine is used ONLY for the wakeword acknowledge (adapter), not masking.
        self.filler_engine = filler_engine
        self.provider = provider or FillerProvider()
        self.policy = policy or FillerPolicy()
        self.interval = interval
        self.backoff = backoff
        self.max_fillers = max_fillers

    async def mask(self, *, stage: str, skill_config: Optional[dict], utterance: str,
                   local_llm, emit_text: EmitText) -> FillerDecision:
        """Speak the opening filler before the result (HIGH speaks nothing)."""
        decision = self.policy.decide(stage, skill_config)
        if decision.kind == "opening":
            text = await self.provider.opening(utterance, local_llm)
            if text:
                await emit_text(text)
        return decision

    async def with_progressive_fillers(self, stream, utterance: str, local_llm):
        """Wrap a streaming skill's output, injecting follow-up fillers during the
        pre-content wait. Yields ``(text, is_filler)``.

        The stream is drained by a pump task; the read timeout cancels only the
        queue ``get``, never the underlying run stream (so the broker live-sink
        and `stream_hermes_reply` stay intact)."""
        if self.max_fillers == 0 or self.interval <= 0:
            async for chunk in stream:
                yield chunk, False
            return

        q: "asyncio.Queue" = asyncio.Queue()

        async def pump():
            try:
                async for chunk in stream:
                    await q.put(("chunk", chunk))
            except Exception as exc:  # surface, don't crash the wrapper
                await q.put(("error", exc))
            finally:
                await q.put(("done", None))

        task = asyncio.create_task(pump())
        idx = 0
        content_started = False
        interval = self.interval
        try:
            while True:
                try:
                    kind, val = await asyncio.wait_for(q.get(), timeout=interval)
                except asyncio.TimeoutError:
                    if not content_started and (self.max_fillers < 0 or idx < self.max_fillers):
                        line = await self.provider.followup(utterance, local_llm, idx)
                        if line:
                            yield line, True
                        idx += 1
                        if self.backoff:
                            interval = self.interval * (1.6 ** idx)   # widen: 3 → ~5 → ~8s
                    continue
                if kind == "done":
                    break
                if kind == "error":
                    logger.error("Streamed skill failed under fillers: %s", val)
                    break
                content_started = True
                yield val, False
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
