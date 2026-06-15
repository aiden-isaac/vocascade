"""
vocascade/skills/base_skills/timers.py — Timers skill (US6 / T234).

Demonstrates the two extension surfaces beyond a one-shot reply:
  • CONVERSE — "set a timer" with no duration asks "for how long?" and claims the
    next utterance via a ConverseClaim.
  • proactive speech — when the timer elapses it speaks through `ctx.notify`
    (idle-gated by the delivery coordinator, like a late Hermes result).
"""

import re
import time
import asyncio
import logging

from vocascade.skills import skill, SkillContext
from vocascade.session.state import ConverseClaim

logger = logging.getLogger("vocascade.skills.base_skills.timers")

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600,
}
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty-five": 45, "sixty": 60, "half": 0,  # "half an hour" handled below
}


def _parse_duration(text: str) -> int | None:
    """Total seconds from phrasings like '5 minutes', 'ten minutes', '1 hour 30 minutes'."""
    low = text.lower()
    if "half an hour" in low or "half hour" in low:
        return 1800
    total = 0
    found = False
    for num, unit in re.findall(r"(\d+|[a-z\-]+)\s+(seconds?|secs?|minutes?|mins?|hours?)", low):
        unit_s = _UNIT_SECONDS.get(unit)
        if unit_s is None:
            continue
        if num.isdigit():
            value = int(num)
        elif num in _WORD_NUMBERS:
            value = _WORD_NUMBERS[num]
        else:
            continue
        total += value * unit_s
        found = True
    return total if (found and total > 0) else None


def _format_duration(seconds: int) -> str:
    parts = []
    for unit, size in (("hour", 3600), ("minute", 60), ("second", 1)):
        n, seconds = divmod(seconds, size)
        if n:
            parts.append(f"{n} {unit}{'s' if n != 1 else ''}")
    return " and ".join(parts) if parts else "0 seconds"


def _start_timer(seconds: int, ctx: SkillContext) -> str:
    label = _format_duration(seconds)
    notify = getattr(ctx, "notify", None)
    if notify is not None:
        async def _fire():
            try:
                await asyncio.sleep(seconds)
                await notify(f"Your {label} timer is up.")
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # never let a stray timer crash anything
                logger.error("Timer notify failed: %s", exc)
        asyncio.create_task(_fire())
    else:
        logger.warning("Timer set but no notify channel — it will not fire.")
    return f"Timer set for {label}."


@skill(
    name="timers",
    keywords=["timer", "set a timer", "set timer", "start a timer"],
    examples=["set a timer for 5 minutes", "set a timer", "timer for 10 minutes",
              "start a 30 second timer"],
)
async def handle_timers(intent: str, entities: dict, ctx: SkillContext) -> str:
    seconds = _parse_duration(intent)
    if seconds is not None:
        return _start_timer(seconds, ctx)

    # No duration → ask, and claim the next utterance (CONVERSE).
    async def _resume(utterance: str, c: SkillContext) -> str:
        secs = _parse_duration(utterance)
        if secs is None:
            return "Sorry, I didn't catch a duration."
        return _start_timer(secs, c)

    if ctx.session is not None:
        ctx.session.converse_claim = ConverseClaim(
            skill_name="timers",
            prompt="for how long?",
            expires_at=time.time() + 30,
            resume=_resume,
        )
    return "For how long?"
