"""
vocascade/skills/base_skills/hermes.py — the Hermes agent skill (US3).

The reference implementation of the bring-your-own-agent pattern: the
waterfall's generic AGENT stage routes here when `waterfall.agent_skill`
names this skill (the default). The handler is a streaming async generator:
it dispatches an always-async run via the app-level TaskBroker
(`ctx.task_broker`) and yields the agent's reply sentence-by-sentence as
`message.delta` fragments arrive (FR-051); a run that finishes after the
conversation has moved on is delivered proactively by the broker (FR-052).

To wire up your own agent, copy this file into user_skills/, swap the broker
calls for your backend, and set `waterfall.agent_skill` in config.yaml.
Nothing here imports from vocascade.waterfall — a copy is self-contained.
"""

import os
import logging
from typing import AsyncIterator

from vocascade.skills import skill, SkillContext
from vocascade.tts.chunker import (
    SpeechChunker,
    drain_segments as _drain_segments,      # re-exported: imported by tests
    clean_for_speech as _clean_for_speech,  # re-exported: imported by tests
)

logger = logging.getLogger("vocascade.skills.base_skills.hermes")


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


@skill(name="hermes",
       available=lambda: bool(os.getenv("HERMES_BASE_URL", "").strip()))
async def handle_hermes(intent: str, entities: dict, ctx: SkillContext):
    broker = getattr(ctx, "task_broker", None)
    if broker is None:
        # No Hermes backend wired (degraded / tests) — speak a graceful line.
        logger.warning("Hermes skill invoked without a TaskBroker; degrading.")
        yield "I can't reach the agent right now."
        return

    session_id = ctx.session.voice_session_id if ctx.session else ""
    async for sentence in stream_hermes_reply(intent, broker, session_id=session_id):
        yield sentence
