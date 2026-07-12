"""
vocascade/skills/context.py — Skill execution context and ToolBag.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Any, Callable, Dict, Awaitable
from vocascade.session.state import SessionState

@dataclass
class Turn:
    """Represents a single conversational turn."""
    request: str
    response: Optional[str] = None
    timestamp: float = 0.0

@dataclass
class ToolBag:
    """Integration tools made available to skill handlers."""
    todoist: Any = None
    calendar: Any = None
    home_assistant: Any = None

@dataclass
class SkillContext:
    """The per-invocation context bundle provided to every skill handler."""
    tools: ToolBag
    session: SessionState
    history: List[Turn] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    emit_filler: Optional[Callable[[str], Awaitable[None]]] = None
    local_llm: Any = None
    # App-level async-run broker (US3): stable SDK surface for ANY skill, not
    # just the bundled agent skill. `await ctx.task_broker.dispatch(prompt,
    # session_id=...)` runs work in the background; late completions are
    # delivered proactively. None when no backend is configured — handle it.
    task_broker: Any = None
    # Proactive delivery (US6): stable SDK surface for any skill. Schedules
    # speech for the next idle moment (idle-gated FIFO), including from
    # background tasks — `await ctx.notify("Your timer is up.")`. None when no
    # delivery is wired.
    notify: Optional[Callable[[str], Awaitable[None]]] = None
