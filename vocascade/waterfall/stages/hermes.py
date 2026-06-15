"""
vocascade/waterfall/stages/hermes.py — HERMES always-async last stage (US3).

The HERMES stage is the waterfall's last resort: anything no local skill claims
is dispatched to the Hermes agent as an asynchronous run (FR-050 — there is no
synchronous Hermes path and no per-query stream-vs-dispatch heuristic). Its
incremental `message.delta` output is streamed into the current turn's TTS for
responsiveness (FR-051); a run that finishes after the conversation has moved on
is delivered proactively by the DeliveryCoordinator (FR-052). Both behaviors run
over the same `/v1/runs` machinery (the TaskBroker + run client), preserved from
005 (FR-053) with a live-streaming sink layered on top.
"""

import re
import logging
from typing import AsyncIterator

from vocascade.waterfall.types import WaterfallStage, ConfidenceResult

logger = logging.getLogger("vocascade.waterfall.stages.hermes")


class HermesStage(WaterfallStage):
    """Absolute fallback: reports confidence 1.0 so it always clears its (0.0)
    threshold when the waterfall reaches it. Routing resolves to the `hermes`
    skill, whose streaming handler drives the actual dispatch."""

    async def evaluate(self, utterance: str, ctx) -> ConfidenceResult:
        if not self.enabled:
            return ConfidenceResult(stage=self.name, confidence=0.0)
        return ConfidenceResult(stage=self.name, confidence=1.0, skill_name="hermes")


# A speakable segment ends at a sentence terminator OR a list-item/line break.
# Agent replies are often markdown (headers, bullet lists, times) with few
# sentence enders, so newlines are first-class boundaries — otherwise the whole
# reply buffers and hits TTS as one giant block (28s+ to first audio).
_SEGMENT_BOUNDARY = re.compile(r"(?<=[.!?:])\s+|\n+")
# Coalesce tiny fragments up to ~a sentence so we don't fire a TTS call per word,
# but flush a long run so no single TTS call swells back into the old problem.
_MIN_SEGMENT_CHARS = 40
_MAX_SEGMENT_CHARS = 220


def _drain_segments(buffer: str):
    """Split a growing buffer into (complete_segments, trailing_remainder).

    A segment is terminated by a sentence ender (. ! ? :) followed by whitespace,
    or by a line break. The unterminated tail stays buffered until more deltas
    arrive (or the stream ends).
    """
    parts = _SEGMENT_BOUNDARY.split(buffer)
    if len(parts) <= 1:
        return [], buffer
    return parts[:-1], parts[-1]


def _clean_for_speech(segment: str) -> str:
    """Strip markdown, list markers, emoji, and odd symbols so TTS speaks the
    content naturally (and faster) instead of reading '**', '-', or '✅' aloud."""
    s = re.sub(r"[*_`#>\[\]]+", "", segment)          # markdown emphasis/headers
    s = s.replace("—", ", ").replace("–", ", ")        # em/en dash → spoken pause
    s = re.sub(r"[^\x00-\x7f]+", "", s)                # drop emoji / non-ASCII
    s = re.sub(r"^[\s\-•*.,]+", "", s)                 # leading bullet/marker noise
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)             # no space before punctuation
    return s


async def stream_hermes_reply(prompt: str, broker, *, session_id: str = "") -> AsyncIterator[str]:
    """Dispatch a Hermes run and yield its reply segment-by-segment as the run's
    `message.delta` fragments arrive (T224), so TTS can start speaking the first
    line while the rest is still streaming/synthesizing.

    On dispatch failure the broker has already queued a proactive failure notice,
    so we yield nothing and let that fire. If this generator is cancelled (the
    user moved on / STOP), the live sink detaches and the broker delivers the
    eventual result proactively instead.
    """
    task = await broker.dispatch(prompt, session_id=session_id)
    if task.is_terminal():
        logger.info("Hermes dispatch terminal at start (%s); proactive notice will deliver", task.state)
        return

    sink = broker.attach_live_sink(task.task_id)
    buffer = ""
    pending = ""
    try:
        while True:
            item = await sink.get()
            if item is broker.LIVE_DONE:
                break
            buffer += item
            complete, buffer = _drain_segments(buffer)
            for seg in complete:
                cleaned = _clean_for_speech(seg)
                if not cleaned:
                    continue
                pending = f"{pending} {cleaned}".strip() if pending else cleaned
                # Force-flush an over-long run at a word boundary.
                while len(pending) >= _MAX_SEGMENT_CHARS:
                    cut = pending.rfind(" ", 0, _MAX_SEGMENT_CHARS)
                    cut = cut if cut > 0 else _MAX_SEGMENT_CHARS
                    head, pending = pending[:cut].strip(), pending[cut:].strip()
                    if head:
                        yield head
                # Otherwise flush once it's a full thought (sentence end) or big
                # enough to be worth a TTS call.
                if pending and (pending[-1] in ".!?:" or len(pending) >= _MIN_SEGMENT_CHARS):
                    yield pending
                    pending = ""
        # Stream ended: flush the buffered remainder + anything pending.
        cleaned = _clean_for_speech(buffer)
        if cleaned:
            pending = f"{pending} {cleaned}".strip() if pending else cleaned
        if pending:
            if pending[-1] not in ".!?:":
                pending += "."
            yield pending
    finally:
        broker.detach_live_sink(task.task_id)
