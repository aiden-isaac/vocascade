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

import logging
from typing import AsyncIterator

from vocascade.waterfall.types import WaterfallStage, ConfidenceResult
from vocascade.tts.chunker import (
    SpeechChunker,
    drain_segments as _drain_segments,      # re-exported: imported by tests
    clean_for_speech as _clean_for_speech,  # re-exported: imported by tests
)

logger = logging.getLogger("vocascade.waterfall.stages.hermes")


class HermesStage(WaterfallStage):
    """Absolute fallback: reports confidence 1.0 so it always clears its (0.0)
    threshold when the waterfall reaches it. Routing resolves to the `hermes`
    skill, whose streaming handler drives the actual dispatch."""

    async def evaluate(self, utterance: str, ctx) -> ConfidenceResult:
        if not self.enabled:
            return ConfidenceResult(stage=self.name, confidence=0.0)
        return ConfidenceResult(stage=self.name, confidence=1.0, skill_name="hermes")


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
    chunker = SpeechChunker()
    try:
        while True:
            item = await sink.get()
            if item is broker.LIVE_DONE:
                break
            for seg in chunker.feed(item):
                yield seg
        # Stream ended: flush the buffered remainder + anything pending.
        for seg in chunker.flush():
            yield seg
    finally:
        broker.detach_live_sink(task.task_id)
