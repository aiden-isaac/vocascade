"""
vocascade/session/state_machine.py — Session lifecycle (US5 / T229).

Drives the explicit passive → active → speaking → passive transitions (FR-060)
over a shared ``SessionState``. Teardown to passive (farewell or silence
timeout) **retains** in-flight background tasks rather than cancelling them
(FR-061) — that is the caller's contract (the broker is never shut down here).
"""

import logging

from vocascade.session.state import SessionState, SessionStateEnum
from vocascade.session.teardown import contains_sentinel

logger = logging.getLogger("vocascade.session.state_machine")


class SessionMachine:
    """Transition helpers over a SessionState. Pure bookkeeping — no I/O."""

    def __init__(self, state: SessionState):
        self.state = state

    @property
    def session_state(self) -> SessionStateEnum:
        return self.state.state

    def _set(self, new: SessionStateEnum) -> None:
        if self.state.state != new:
            logger.info("Session %s: %s → %s", self.state.voice_session_id or "?",
                        self.state.state.value, new.value)
            self.state.state = new

    # ── lifecycle transitions ────────────────────────────────────────────────

    def on_wake(self) -> None:
        self.state.wake_count += 1
        self.state.teardown_armed = False
        self.state.reset_activity()
        self._set(SessionStateEnum.ACTIVE)

    def on_user_engaged(self) -> None:
        """User started speaking — re-engagement disarms any pending teardown."""
        self.state.teardown_armed = False
        self.state.reset_activity()

    def on_bot_started(self) -> None:
        self._set(SessionStateEnum.SPEAKING)

    def on_bot_stopped(self) -> None:
        if self.state.state == SessionStateEnum.SPEAKING:
            self._set(SessionStateEnum.ACTIVE)
        self.state.reset_activity()

    def on_teardown(self) -> None:
        """Farewell or silence timeout → passive. In-flight tasks are retained."""
        self.state.teardown_armed = False
        self._set(SessionStateEnum.PASSIVE)

    def on_stop(self) -> None:
        """STOP cancelled in-flight work; remain in active listening."""
        self.state.teardown_armed = False
        self._set(SessionStateEnum.ACTIVE)

    # ── teardown arming ──────────────────────────────────────────────────────

    def arm_teardown(self) -> None:
        self.state.teardown_armed = True

    def note_reply(self, text: str) -> None:
        """Arm teardown if the assistant reply carried the ENDSESSION sentinel."""
        if text and contains_sentinel(text):
            self.state.teardown_armed = True

    @property
    def should_teardown(self) -> bool:
        return self.state.teardown_armed
