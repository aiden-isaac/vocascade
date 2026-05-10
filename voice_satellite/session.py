"""
voice_satellite/session.py — Conversation state machine.

One ConversationSession is created per WebSocket connection. It tracks:
  - Current state (passive_listening ↔ active_listening ↔ speaking ↔ ...)
  - All cancellable asyncio tasks (generation, silence timer)
  - Barge-in context (words played before interrupt)
"""

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

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


class ConversationSession:
    """
    Tracks all mutable per-connection state. Thread-safe via asyncio (single-threaded
    event loop). Methods that cancel tasks always await cancellation to completion.
    """

    def __init__(self, silence_timeout: float = DEFAULT_SILENCE_TIMEOUT) -> None:
        self.state: SessionState = SessionState.PASSIVE_LISTENING
        self.silence_timeout: float = silence_timeout

        # The main handle_audio coroutine task (STT → LLM → TTS chain).
        self.generation_task: asyncio.Task | None = None

        # Fires when the silence timer expires in active_listening.
        self.silence_timer: asyncio.Task | None = None

        # Callback invoked when silence expires. Set by the WebSocket handler.
        # Signature: async () -> None
        self._on_silence_expire: "asyncio.coroutines._CoroutineType | None" = None  # type: ignore[name-defined]

        # Barge-in context: populated by synthesize_sentence via set_current_response().
        # Enables reconstructing the partial utterance the user actually heard.
        self._current_response_words: list[str] = []
        self._words_played_before_interrupt: int = 0

        # Last user transcript in this turn (for partial context recording).
        self.last_transcript: str = ""

    # ──────────────────────────────────────────────────────────────────────────
    # State management
    # ──────────────────────────────────────────────────────────────────────────

    def is_passive(self) -> bool:
        return self.state == SessionState.PASSIVE_LISTENING

    def is_active(self) -> bool:
        return self.state in {
            SessionState.ACTIVE_LISTENING,
            SessionState.TRANSCRIBING,
            SessionState.THINKING,
            SessionState.FILLER_SPEAKING,
            SessionState.SPEAKING,
        }

    def is_busy(self) -> bool:
        """True while generating a response (LLM + TTS pipeline running)."""
        return self.state in {
            SessionState.THINKING,
            SessionState.FILLER_SPEAKING,
            SessionState.SPEAKING,
        }

    def set_state(self, new_state: SessionState) -> None:
        if self.state != new_state:
            logger.info("Session: %s → %s", self.state.value, new_state.value)
            self.state = new_state

    # ──────────────────────────────────────────────────────────────────────────
    # Silence timer
    # ──────────────────────────────────────────────────────────────────────────

    def set_silence_callback(self, coro_factory: "callable") -> None:  # type: ignore[type-arg]
        """
        Register a zero-argument async factory (or coroutine) to call when the
        silence timer expires. Called from server.py after session creation.
        """
        self._on_silence_expire = coro_factory

    def reset_silence_timer(self) -> None:
        """Restart the inactivity timer. Call on every user speech event."""
        if self.silence_timer and not self.silence_timer.done():
            self.silence_timer.cancel()
        self.silence_timer = asyncio.create_task(self._run_silence_timer())

    def cancel_silence_timer(self) -> None:
        if self.silence_timer and not self.silence_timer.done():
            self.silence_timer.cancel()
            self.silence_timer = None

    async def _run_silence_timer(self) -> None:
        try:
            await asyncio.sleep(self.silence_timeout)
            logger.info("Silence timeout after %.0fs — returning to passive", self.silence_timeout)
            if self._on_silence_expire is not None:
                await self._on_silence_expire()
        except asyncio.CancelledError:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # Generation task management
    # ──────────────────────────────────────────────────────────────────────────

    def set_generation_task(self, task: asyncio.Task) -> None:
        self.generation_task = task

    async def cancel_generation(self) -> str:
        """
        Cancel the active generation task (LLM + TTS pipeline).
        Returns the partial utterance the user actually heard, based on
        words_played_before_interrupt reported by the frontend.
        """
        if self.generation_task and not self.generation_task.done():
            self.generation_task.cancel()
            try:
                await self.generation_task
            except (asyncio.CancelledError, Exception):
                pass
        self.generation_task = None

        # Reconstruct partial utterance from word offset
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

    # ──────────────────────────────────────────────────────────────────────────
    # Barge-in context tracking
    # ──────────────────────────────────────────────────────────────────────────

    def set_current_response(self, full_text: str) -> None:
        """Called by server.py when the full LLM response is known."""
        self._current_response_words = full_text.split()
        self._words_played_before_interrupt = 0

    def update_words_played(self, words_played: int) -> None:
        """Called when a playback_progress message arrives from the frontend."""
        self._words_played_before_interrupt = words_played

    # ──────────────────────────────────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Cancel all tasks on WebSocket disconnect."""
        self.cancel_silence_timer()
        if self.generation_task and not self.generation_task.done():
            self.generation_task.cancel()
            try:
                await self.generation_task
            except (asyncio.CancelledError, Exception):
                pass
