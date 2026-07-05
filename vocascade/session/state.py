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
    # D4: the specific "can't reach my language model" notice is spoken at most
    # once per session; later classified LLM failures use the generic fallback.
    llm_failure_notified: bool = False
    # Per-session turn log (user utterance + spoken reply + winning stage) used to
    # build the session-end memory gist (US10 / FR-090). Not a full transcript.
    turns: list = field(default_factory=list)

    def reset_activity(self):
        """Update last activity timestamp to the current time."""
        self.last_activity_at = time.time()

    def record_turn(self, user: str, assistant: str, stage: str = "") -> None:
        """Append one user→assistant exchange for the end-of-session summary."""
        if user and assistant:
            self.turns.append({"user": user, "assistant": assistant, "stage": stage})
