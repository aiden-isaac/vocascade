"""
vocascade/skills/base_skills/stop.py — STOP / farewell handler (US5/US6 / T236).

The STOP/SYSTEM stage classifies "stop"/"cancel" and farewells and routes here
with the action in `entities`. Verbal/explicit stop cancels the session's
in-flight Hermes runs and releases any multi-turn claim; a farewell arms the
teardown so the session returns to passive after the sign-off is spoken.
"""

import logging

from vocascade.skills import skill, SkillContext

logger = logging.getLogger("vocascade.skills.base_skills.stop")

# Hooks fired on an explicit "stop" so a skill can silence something it started
# outside the conversation (e.g. a user alarm/rain-noise subprocess). Each hook
# is a zero-arg callable returning truthy if it actually stopped something. A
# no-op when nothing registers — keeps STOP decoupled from optional user skills.
_STOP_HOOKS = []  # ponytail: process-wide list; alarm skill registers stop_all here


def register_stop_hook(fn):
    _STOP_HOOKS.append(fn)


@skill(name="stop")
async def handle_stop(intent: str, entities: dict, ctx: SkillContext) -> str:
    action = (entities or {}).get("action", "stop")

    if action == "farewell":
        if ctx.session is not None:
            ctx.session.teardown_armed = True
        return "Goodbye."

    # STOP: cancel this session's in-flight Hermes runs (FR-070/071). A barge-in
    # may have already cancelled them (then this is a no-op); an explicit "stop"
    # with no barge-in is handled here.
    broker = getattr(ctx, "task_broker", None)
    if broker is not None and ctx.session is not None:
        sid = ctx.session.voice_session_id
        for task in list(broker.active_tasks()):
            if not sid or task.session_id == sid:
                await broker.cancel(task.task_id)
    if ctx.session is not None:
        ctx.session.converse_claim = None   # STOP releases any claim (FR-081)

    # Silence anything a skill is playing outside the conversation (e.g. a ringing
    # alarm). Returns "Stopped." if a hook actually killed something.
    silenced = False
    for hook in _STOP_HOOKS:
        try:
            silenced = bool(hook()) or silenced
        except Exception as exc:
            logger.error("Stop hook failed: %s", exc)
    return "Stopped." if silenced else "Okay."
