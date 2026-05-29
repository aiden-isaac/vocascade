"""
Conversation state machine and session tracker for the voice satellite client.
"""

import asyncio
import inspect
import logging
from enum import Enum
from typing import Callable, Any

logger = logging.getLogger("voice_satellite.session")

DEFAULT_SILENCE_TIMEOUT = 30.0  # seconds

class SessionState(str, Enum):
    PASSIVE_LISTENING = "passive_listening"
    ACKNOWLEDGING = "acknowledging"
    ACTIVE_LISTENING = "active_listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    FILLER_SPEAKING = "filler_speaking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"

VALID_TRANSITIONS = {
    SessionState.PASSIVE_LISTENING: {
        SessionState.ACKNOWLEDGING,
        SessionState.PASSIVE_LISTENING,
    },
    SessionState.ACKNOWLEDGING: {
        SessionState.ACTIVE_LISTENING,
        SessionState.PASSIVE_LISTENING,
    },
    SessionState.ACTIVE_LISTENING: {
        SessionState.TRANSCRIBING,
        SessionState.PASSIVE_LISTENING,
    },
    SessionState.TRANSCRIBING: {
        SessionState.THINKING,
        SessionState.ACTIVE_LISTENING,
        SessionState.PASSIVE_LISTENING,
    },
    SessionState.THINKING: {
        SessionState.SPEAKING,
        SessionState.FILLER_SPEAKING,
        SessionState.PASSIVE_LISTENING,
        SessionState.ACTIVE_LISTENING,
        SessionState.INTERRUPTED,
    },
    SessionState.FILLER_SPEAKING: {
        SessionState.SPEAKING,
        SessionState.INTERRUPTED,
        SessionState.ACTIVE_LISTENING,
    },
    SessionState.SPEAKING: {
        SessionState.ACTIVE_LISTENING,
        SessionState.INTERRUPTED,
        SessionState.FILLER_SPEAKING,
    },
    SessionState.INTERRUPTED: {
        SessionState.ACTIVE_LISTENING,
    }
}

class ConversationSession:
    """
    Tracks per-session states, transition rules, and silence timeouts.
    """
    def __init__(self, silence_timeout: float = DEFAULT_SILENCE_TIMEOUT) -> None:
        self._state: SessionState = SessionState.PASSIVE_LISTENING
        self.silence_timeout: float = silence_timeout
        
        self.generation_task: asyncio.Task | None = None
        self.silence_timer: asyncio.Task | None = None
        
        self._on_silence_expire: Callable[[], Any] | None = None
        self._on_state_change: Callable[[SessionState, SessionState], None] | None = None
        
        self._current_response_words: list[str] = []
        self._words_played_before_interrupt: int = 0
        self.last_transcript: str = ""
        self.audio_buffer: bytes = b""
        self.history: list[dict[str, str]] = []

    @property
    def state(self) -> SessionState:
        return self._state

    @state.setter
    def state(self, new_state: SessionState) -> None:
        self.transition(new_state)

    def transition(self, new_state: SessionState) -> None:
        """
        Transitions the session to a new state if permitted by validation rules.
        """
        if self._state == new_state:
            return

        is_valid = (
            new_state == SessionState.PASSIVE_LISTENING or
            new_state in VALID_TRANSITIONS.get(self._state, set())
        )

        if not is_valid:
            raise ValueError(f"Invalid state transition: {self._state.value} -> {new_state.value}")

        old_state = self._state
        self._state = new_state
        logger.info(f"Session state transition: {old_state.value} -> {new_state.value}")

        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state)
            except Exception as e:
                logger.error(f"Error in state change callback: {e}", exc_info=True)

    def is_passive(self) -> bool:
        return self._state == SessionState.PASSIVE_LISTENING

    def is_active(self) -> bool:
        return self._state in {
            SessionState.ACTIVE_LISTENING,
            SessionState.TRANSCRIBING,
            SessionState.THINKING,
            SessionState.FILLER_SPEAKING,
            SessionState.SPEAKING,
        }

    def is_busy(self) -> bool:
        return self._state in {
            SessionState.THINKING,
            SessionState.FILLER_SPEAKING,
            SessionState.SPEAKING,
        }

    def set_state_change_callback(self, callback: Callable[[SessionState, SessionState], None]) -> None:
        self._on_state_change = callback

    def set_silence_callback(self, callback: Callable[[], Any]) -> None:
        self._on_silence_expire = callback

    def start_silence_timer(self) -> None:
        """Starts the silence timer."""
        self.cancel_silence_timer()
        self.silence_timer = asyncio.create_task(self._run_silence_timer())

    def reset_silence_timer(self) -> None:
        """Restarts the silence timer."""
        self.start_silence_timer()

    def cancel_silence_timer(self) -> None:
        if self.silence_timer and not self.silence_timer.done():
            self.silence_timer.cancel()
        self.silence_timer = None
    async def _run_silence_timer(self) -> None:
        try:
            await asyncio.sleep(self.silence_timeout)
            logger.info("Silence timeout after %.0fs — returning to passive", self.silence_timeout)
            if self._on_silence_expire:
                if inspect.iscoroutinefunction(self._on_silence_expire):
                    await self._on_silence_expire()
                else:
                    self._on_silence_expire()
        except asyncio.CancelledError:
            pass
        finally:
            if self.silence_timer is asyncio.current_task():
                self.silence_timer = None

    def set_generation_task(self, task: asyncio.Task) -> None:
        self.generation_task = task

    async def cancel_generation(self) -> str:
        if self.generation_task and not self.generation_task.done():
            self.generation_task.cancel()
            try:
                await self.generation_task
            except (asyncio.CancelledError, Exception):
                pass
        self.generation_task = None

        if self._current_response_words and self._words_played_before_interrupt > 0:
            n = min(self._words_played_before_interrupt, len(self._current_response_words))
            partial = " ".join(self._current_response_words[:n])
            logger.info(
                "Barge-in: %d/%d words played → %r...",
                n,
                len(self._current_response_words),
                partial[:60],
            )
            return partial
        return ""

    def set_current_response(self, full_text: str) -> None:
        self._current_response_words = full_text.split()
        self._words_played_before_interrupt = 0

    def append_current_response(self, text: str) -> None:
        self._current_response_words.extend(text.split())

    def update_words_played(self, words_played: int) -> None:
        self._words_played_before_interrupt = words_played

    async def close(self) -> None:
        self.cancel_silence_timer()
        await self.cancel_generation()
