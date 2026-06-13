"""
DeliveryCoordinator — the single owner of system-initiated speech (spec 005).

All proactive output (task results, failure notices, approval requests,
backlog announcements) flows through one idle-gated FIFO queue. Nothing is
spoken while the user or the bot is talking; results wait for a quiet
channel and are injected through the bound sink (AdapterProcessor.inject_text).
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("vocascade.delivery")

# Sink: async callable that injects text into the live pipeline for TTS.
InjectFn = Callable[[str], Awaitable[None]]
# Optional condenser: full text -> speech-sized text (one local-LLM call, T117).
CondenseFn = Callable[[str], Awaitable[str]]


class DeliveryKind(str, Enum):
    RESULT = "result"
    FAILURE_NOTICE = "failure_notice"
    APPROVAL_REQUEST = "approval_request"
    BACKLOG_ANNOUNCEMENT = "backlog_announcement"


class DeliveryState(str, Enum):
    QUEUED = "queued"
    SPEAKING = "speaking"
    DELIVERED = "delivered"
    INTERRUPTED = "interrupted"


@dataclass
class ProactiveResult:
    task_id: str
    kind: DeliveryKind
    preamble: str
    speech_text: str
    full_text: str
    enqueued_at: float = field(default_factory=time.time)
    state: DeliveryState = DeliveryState.QUEUED


def make_preamble(request_text: str, max_words: int = 6) -> str:
    """Re-engagement phrase derived from the originating request.

    "what's on my schedule today" -> "About your request — what's on my
    schedule today: "
    """
    words = request_text.strip().rstrip("?.!").split()
    if not words:
        return "About your earlier request: "
    short = " ".join(words[:max_words])
    if len(words) > max_words:
        short += "…"
    return f"About your request — {short}: "


class DeliveryCoordinator:
    """Idle-gated FIFO delivery of ProactiveResults.

    Channel-idle state is maintained by notify_* hooks fed from pipeline
    frames; an item is dequeued only when the channel is fully quiet and a
    session is active. One item is in flight at a time: after injection the
    coordinator waits for the bot-stopped-speaking notification before
    considering the next item.
    """

    def __init__(
        self,
        *,
        speech_budget: int = 600,
        condenser: Optional[CondenseFn] = None,
        on_delivered: Optional[Callable[[ProactiveResult], None]] = None,
    ) -> None:
        self.queue: deque[ProactiveResult] = deque()
        self.speech_budget = speech_budget
        self.condenser = condenser
        self.on_delivered = on_delivered
        self._inject: InjectFn | None = None
        self._user_speaking = False
        self._bot_speaking = False
        self._awaiting_response = False  # user turn sent to LLM, reply pending
        self._session_active = False
        self._speaking_item: ProactiveResult | None = None
        self._delivering = False

    # ── session binding ──────────────────────────────────────────────────────

    def bind_session(self, inject: InjectFn) -> None:
        """A voice session became active; deliveries flow through `inject`."""
        self._inject = inject
        self._session_active = True
        self._user_speaking = False
        self._bot_speaking = False
        self._awaiting_response = False
        self._maybe_deliver()

    def unbind_session(self) -> None:
        """Session ended (teardown/disconnect). Queued items are retained for
        the next session; an in-flight item stays SPEAKING→INTERRUPTED."""
        self._session_active = False
        self._inject = None
        if self._speaking_item is not None:
            # The channel died under the item; treat as interrupted, no re-queue.
            self._finish_speaking(interrupted=True)

    # ── pipeline notifications ───────────────────────────────────────────────

    def notify_user_started_speaking(self) -> None:
        self._user_speaking = True
        if self._speaking_item is not None:
            # Barge-in during delivery: stop; the transcript interceptor
            # commits "[interrupted]"; the item is NOT re-queued.
            self._finish_speaking(interrupted=True)

    def notify_interruption(self) -> None:
        """Explicit client interrupt (barge-in signal without VAD framing):
        kill an in-flight delivery but don't latch the user-speaking flag —
        the actual speech, if any, arrives with its own VAD start/stop."""
        if self._speaking_item is not None:
            self._finish_speaking(interrupted=True)

    def notify_user_stopped_speaking(self) -> None:
        # VAD-stop alone doesn't mean a reply is coming (silence and noise
        # also produce stop frames); the response-pending gate is raised by
        # notify_response_pending when a transcription actually reaches the
        # LLM.
        self._user_speaking = False
        self._maybe_deliver()

    def notify_response_pending(self) -> None:
        """A user transcription was handed to the LLM; the channel stays busy
        until the bot's reply finishes (BotStoppedSpeaking)."""
        self._awaiting_response = True

    def notify_bot_started_speaking(self) -> None:
        self._bot_speaking = True

    def notify_bot_stopped_speaking(self) -> None:
        self._bot_speaking = False
        self._awaiting_response = False
        if self._speaking_item is not None:
            self._finish_speaking(interrupted=False)
        self._maybe_deliver()

    # ── enqueue ──────────────────────────────────────────────────────────────

    @property
    def is_idle(self) -> bool:
        return (
            self._session_active
            and self._inject is not None
            and not self._user_speaking
            and not self._bot_speaking
            and not self._awaiting_response
            and self._speaking_item is None
        )

    def gate_state(self) -> str:
        return (
            f"session={self._session_active} user_speaking={self._user_speaking} "
            f"bot_speaking={self._bot_speaking} awaiting_response={self._awaiting_response} "
            f"in_flight={self._speaking_item is not None}"
        )

    def enqueue(self, result: ProactiveResult) -> None:
        logger.info(
            "Delivery queued: %s for %s (%d queued) — gate: %s",
            result.kind.value, result.task_id, len(self.queue) + 1, self.gate_state(),
        )
        self.queue.append(result)
        self._maybe_deliver()

    def pending(self) -> list[ProactiveResult]:
        return list(self.queue)

    # ── delivery machinery ───────────────────────────────────────────────────

    def _maybe_deliver(self) -> None:
        if self._delivering or not self.queue or not self.is_idle:
            return
        try:
            self._delivering = True
            asyncio.create_task(self._deliver_next())
        except RuntimeError:
            # No running loop (e.g. early startup); next notify retries.
            self._delivering = False

    async def _deliver_next(self) -> None:
        try:
            if not self.queue or not self.is_idle:
                return
            item = self.queue.popleft()
            speech = item.speech_text
            if self.condenser is not None and len(speech) > self.speech_budget:
                try:
                    speech = await self.condenser(item.full_text)
                    item.speech_text = speech
                except Exception:
                    logger.exception("Condensation failed; speaking full text")
            item.state = DeliveryState.SPEAKING
            self._speaking_item = item
            inject = self._inject
            if inject is None:  # session vanished between checks
                item.state = DeliveryState.QUEUED
                self.queue.appendleft(item)
                self._speaking_item = None
                return
            logger.info("Delivering %s for %s", item.kind.value, item.task_id)
            await inject(f"{item.preamble}{speech}")
            # Item completes on notify_bot_stopped_speaking (or barge-in).
        except Exception:
            logger.exception("Delivery failed")
            self._speaking_item = None
        finally:
            self._delivering = False

    def _finish_speaking(self, *, interrupted: bool) -> None:
        item = self._speaking_item
        if item is None:
            return
        self._speaking_item = None
        item.state = (
            DeliveryState.INTERRUPTED if interrupted else DeliveryState.DELIVERED
        )
        logger.info(
            "Delivery %s: %s for %s",
            item.state.value, item.kind.value, item.task_id,
        )
        if self.on_delivered is not None:
            try:
                self.on_delivered(item)
            except Exception:
                logger.exception("on_delivered callback failed")
