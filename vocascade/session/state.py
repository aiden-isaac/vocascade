"""
vocascade/session/state.py — Session state and lifecycle structures.
"""

import asyncio
import time
from enum import Enum
from typing import Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field

class SessionStateEnum(str, Enum):
    PASSIVE = "passive"
    ACTIVE = "active"
    SPEAKING = "speaking"

@dataclass
class ConverseClaim:
    """A claim by a skill to capture the next utterance in a multi-turn conversation."""
    skill_name: str
    prompt: str
    expires_at: float
    # resume(utterance, ctx) -> str or Stream
    resume: Callable[[str, Any], Awaitable[Any]]

@dataclass
class SessionState:
    """The state and lifecycle metadata of an active voice session."""
    state: SessionStateEnum = SessionStateEnum.PASSIVE
    converse_claim: Optional[ConverseClaim] = None
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)
    voice_session_id: str = ""
    wake_count: int = 0
    last_activity_at: float = field(default_factory=time.time)
    # Armed by a farewell phrase or the model ENDSESSION sentinel; teardown to
    # passive fires after the current reply finishes speaking (US5 / FR-062).
    teardown_armed: bool = False

    def reset_activity(self):
        """Update last activity timestamp to the current time."""
        self.last_activity_at = time.time()
