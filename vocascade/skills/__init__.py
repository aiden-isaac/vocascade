"""
vocascade/skills — Skill SDK initialization.
"""

from typing import List, Optional, Callable, Any, Dict
from vocascade.skills.registry import registry, Skill
from vocascade.skills.context import SkillContext, ToolBag, Turn

def skill(
    name: str,
    examples: Optional[List[str]] = None,
    keywords: Optional[List[str]] = None,
    confidence: Optional[Callable[[str], float]] = None,
    available: Optional[Callable[[], bool]] = None,
):
    """
    Decorator to declare and register a skill with the global registry.

    `available` is an optional zero-arg gate: return falsy (or raise — logged,
    never fatal) to mark the skill unavailable for role claims such as
    `waterfall.agent_skill`. Omitted means always available.
    """
    def decorator(func):
        registry.register(
            name=name,
            handler=func,
            examples=examples,
            keywords=keywords,
            confidence=confidence,
            available=available,
        )
        return func
    return decorator

__all__ = ["skill", "registry", "Skill", "SkillContext", "ToolBag", "Turn"]
