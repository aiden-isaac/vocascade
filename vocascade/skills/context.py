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
