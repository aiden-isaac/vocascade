"""
vocascade/skills/base_skills/datetime.py — Date / time skill (US6 / T235).

A fast, local, deterministic skill: keyword-matched at the HIGH stage, answered
without any LLM or network. "what time is it" → the time; "what's the date" →
the date.
"""

import datetime as _dt

from vocascade.skills import skill, SkillContext


def _fmt_time(now: _dt.datetime) -> str:
    # %-I/%-d are no-leading-zero (Linux); fall back gracefully elsewhere.
    try:
        return now.strftime("It's %-I:%M %p.")
    except ValueError:
        return now.strftime("It's %I:%M %p.").replace(" 0", " ")


def _fmt_date(now: _dt.datetime) -> str:
    try:
        return now.strftime("Today is %A, %B %-d, %Y.")
    except ValueError:
        return now.strftime("Today is %A, %B %d, %Y.")


@skill(
    name="datetime",
    keywords=["time", "date", "clock", "what time", "what day", "today's date"],
    examples=["what time is it", "what is the date today", "what day is it",
              "tell me the time", "what's today's date"],
)
async def handle_datetime(intent: str, entities: dict, ctx: SkillContext) -> str:
    now = _dt.datetime.now()
    low = intent.lower()
    wants_date = "date" in low or "day" in low
    wants_time = "time" in low or "clock" in low or "o'clock" in low
    if wants_date and not wants_time:
        return _fmt_date(now)
    if wants_time and not wants_date:
        return _fmt_time(now)
    # Ambiguous / both → give both.
    return f"{_fmt_time(now)[:-1]} on {now.strftime('%A, %B')} {now.day}."
