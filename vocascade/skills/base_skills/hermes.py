"""
vocascade/skills/base_skills/hermes.py — the Hermes agent skill (US3).

The waterfall's HERMES stage routes here when no local skill claims an
utterance. The handler is a streaming async generator: it dispatches an async
run via the app-level TaskBroker (carried on the SkillContext) and yields the
agent's reply sentence-by-sentence as `message.delta` fragments arrive. Late
completions fall back to proactive delivery inside the broker.
"""

import logging

from vocascade.skills import skill, SkillContext
from vocascade.waterfall.stages.hermes import stream_hermes_reply

logger = logging.getLogger("vocascade.skills.base_skills.hermes")


@skill(name="hermes")
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
