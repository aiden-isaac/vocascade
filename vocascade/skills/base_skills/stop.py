"""
vocascade/skills/base_skills/stop.py — STOP / farewell handler (US5/US6 / T236).

The STOP/SYSTEM stage classifies "stop"/"cancel" and farewells and routes here
with the action in `entities`. Verbal/explicit stop cancels the session's
in-flight Hermes runs and releases any multi-turn claim; a farewell arms the
teardown so the session returns to passive after the sign-off is spoken.
"""

from vocascade.skills import skill, SkillContext


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
    return "Okay."
