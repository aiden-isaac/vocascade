"""
vocascade/waterfall/types.py — Waterfall stage types and ABC.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any, Dict

@dataclass
class ConfidenceResult:
    """Verdict of a waterfall routing stage for a given utterance."""
    stage: str
    confidence: float  # 0.0 to 1.0
    skill_name: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

class WaterfallStage(ABC):
    """Abstract Base Class representing a confidence waterfall routing stage."""

    def __init__(self, name: str, threshold: float = 0.5, enabled: bool = True):
        self.name = name
        self.threshold = threshold
        self.enabled = enabled

    @abstractmethod
    async def evaluate(self, utterance: str, ctx: Any) -> ConfidenceResult:
        """
        Evaluate the utterance within the given SkillContext and return a ConfidenceResult.
        """
        pass
