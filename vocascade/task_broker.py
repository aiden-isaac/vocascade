"""
TaskBroker — owns the HermesTask registry and run lifecycles (spec 005).

dispatch() creates a task (pending), starts a run on Hermes, flips it to
executing, and spawns one consumer per run that drains the per-run SSE
stream and applies an idempotent state machine. Terminal events become
ProactiveResults handed to the DeliveryCoordinator. When the server lacks
the runs API, the same task semantics wrap the buffered chat-completions
fallback stream (one synthesized completion).

The journal (US6/T134) is intentionally absent at this stage.
"""

import asyncio
import datetime
import logging
import time
from typing import Optional

from vocascade.delivery import (
    DeliveryCoordinator,
    DeliveryKind,
    ProactiveResult,
    make_preamble,
)
from vocascade.hermes_run_client import (
    HermesRunClient,
    RunDispatchError,
    RunEvent,
    RunEventKind,
)
from vocascade.transcript_manager import (
    HermesTask,
    HermesTaskState,
    TranscriptManager,
)

logger = logging.getLogger("vocascade.task_broker")

_FAILURE_SPEECH = "I wasn't able to finish that request — the agent reported a failure."
_DISPATCH_FAILURE_SPEECH = "I couldn't hand that off to the agent. It may be unreachable."


def _generate_task_id(counter: int) -> str:
    now = datetime.datetime.now()
    return f"task_{now.strftime('%Y%m%d_%H%M%S')}_{counter:02d}"


# Sentinel pushed onto a live sink when its run reaches a terminal state.
LIVE_DONE = object()


class TaskBroker:
    # Re-exported so a live consumer can compare against `broker.LIVE_DONE`.
    LIVE_DONE = LIVE_DONE

    def __init__(
        self,
        run_client: HermesRunClient,
        delivery: DeliveryCoordinator,
        transcript_manager: Optional[TranscriptManager] = None,
    ) -> None:
        self.run_client = run_client
        self.delivery = delivery
        self.transcript_manager = transcript_manager
        self.tasks: dict[str, HermesTask] = {}
        self._by_run_id: dict[str, str] = {}
        self._consumers: dict[str, asyncio.Task] = {}
        self._counter = 0
        # US3 live streaming: a turn may attach a sink to receive a run's
        # message.delta fragments in real time (T224). While a sink is attached
        # the run's terminal result is delivered live, not proactively.
        self._live_sinks: dict[str, "asyncio.Queue"] = {}
        self._delta_streamed: set[str] = set()
        # Wire delivered-state back into the registry.
        if delivery.on_delivered is None:
            delivery.on_delivered = self._on_delivered

    # ── live streaming sink (US3 / T224) ─────────────────────────────────────

    def attach_live_sink(self, task_id: str) -> "asyncio.Queue":
        """Stream this task's incremental output into the returned queue.

        Attach immediately after `dispatch()` returns (before awaiting), so the
        sink is registered before the run consumer first runs. The queue yields
        delta text fragments, then `LIVE_DONE` on terminal.
        """
        q: "asyncio.Queue" = asyncio.Queue()
        self._live_sinks[task_id] = q
        return q

    def detach_live_sink(self, task_id: str) -> None:
        """Stop live-streaming a task. If the run is still in flight, its result
        will be delivered proactively instead (the conversation moved on)."""
        self._live_sinks.pop(task_id, None)

    # ── session wiring ───────────────────────────────────────────────────────

    def bind_transcript_manager(self, manager: Optional[TranscriptManager]) -> None:
        """Sessions come and go; the broker outlives them. Tag updates flow to
        whichever transcript manager is currently live."""
        self.transcript_manager = manager

    # ── registry views ───────────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[HermesTask]:
        return self.tasks.get(task_id)

    def get_task_by_run(self, run_id: str) -> Optional[HermesTask]:
        task_id = self._by_run_id.get(run_id)
        return self.tasks.get(task_id) if task_id else None

    def active_tasks(self) -> list[HermesTask]:
        return [t for t in self.tasks.values() if not t.is_terminal()]

    # ── dispatch ─────────────────────────────────────────────────────────────

    async def dispatch(self, request_text: str, session_id: str = "") -> HermesTask:
        """Create a task and start a Hermes run (or the chat fallback).

        Always returns the task; on dispatch failure the task comes back
        `failed` with a FAILURE_NOTICE queued, never raises.
        """
        self._counter += 1
        task = HermesTask(
            task_id=_generate_task_id(self._counter),
            created_at=time.time(),
            state=HermesTaskState.PENDING,
            request_text=request_text,
            session_id=session_id,
        )
        self.tasks[task.task_id] = task

        caps = await self.run_client.probe_capabilities()
        if not caps.supports_runs:
            logger.warning(
                "Runs API unavailable — task %s uses chat fallback", task.task_id
            )
            self._set_state(task, HermesTaskState.EXECUTING)
            self._consumers[task.task_id] = asyncio.create_task(
                self._consume_fallback(task)
            )
            return task

        try:
            handle = await self.run_client.start_run(
                request_text, session_id=session_id
            )
        except RunDispatchError as exc:
            logger.error("Dispatch failed for %s: %s", task.task_id, exc)
            self._set_state(task, HermesTaskState.FAILED)
            self._enqueue_notice(task, _DISPATCH_FAILURE_SPEECH)
            return task

        task.run_id = handle.run_id
        self._by_run_id[handle.run_id] = task.task_id
        self._set_state(task, HermesTaskState.EXECUTING)
        logger.info("Task %s dispatched as run %s", task.task_id, handle.run_id)
        self._consumers[task.task_id] = asyncio.create_task(self._consume_run(task))
        return task

    # ── cancellation (full tool flow lands in US5) ───────────────────────────

    async def cancel(self, task_id: str) -> bool:
        """Optimistically cancel; returns True if a cancel was issued."""
        task = self.tasks.get(task_id)
        if task is None or task.is_terminal():
            return False
        self._set_state(task, HermesTaskState.CANCELLED)
        if task.run_id:
            try:
                await self.run_client.stop_run(task.run_id)
            except Exception as exc:
                logger.warning("stop_run(%s) failed: %s", task.run_id, exc)
        consumer = self._consumers.pop(task_id, None)
        if consumer is not None and not consumer.done():
            consumer.cancel()
        # Unblock any live streaming turn still waiting on this run's sink (US5 STOP).
        sink = self._live_sinks.pop(task_id, None)
        if sink is not None:
            sink.put_nowait(self.LIVE_DONE)
        self._delta_streamed.discard(task_id)
        return True

    async def shutdown(self) -> None:
        for consumer in self._consumers.values():
            if not consumer.done():
                consumer.cancel()
        self._consumers.clear()

    # ── consumers ────────────────────────────────────────────────────────────

    async def _consume_run(self, task: HermesTask) -> None:
        assert task.run_id is not None
        try:
            async for event in self.run_client.stream_events(
                task.run_id, session_id=task.session_id
            ):
                self.apply_event(task.task_id, event)
                if event.is_terminal:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Consumer for %s died; marking failed", task.task_id)
            self._apply_terminal(task, HermesTaskState.FAILED, None)
        finally:
            self._consumers.pop(task.task_id, None)

    async def _consume_fallback(self, task: HermesTask) -> None:
        """Chat-completions fallback: buffer the stream, synthesize ONE
        completion when it closes."""
        chunks: list[str] = []
        try:
            async for chunk in self.run_client.chat_fallback(
                task.request_text, session_id=task.session_id
            ):
                chunks.append(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Fallback stream for %s failed", task.task_id)
            self._apply_terminal(task, HermesTaskState.FAILED, None)
            return
        finally:
            self._consumers.pop(task.task_id, None)
        result = "".join(chunks).strip()
        if result:
            self._apply_terminal(task, HermesTaskState.COMPLETED, result)
        else:
            self._apply_terminal(task, HermesTaskState.FAILED, None)

    # ── idempotent state machine ─────────────────────────────────────────────

    def apply_event(self, task_id: str, event: RunEvent) -> None:
        """Apply one run event. Duplicate, out-of-order, or post-terminal
        events log and no-op — transitions happen out of pending/executing
        only."""
        task = self.tasks.get(task_id)
        if task is None:
            logger.warning("Event %s for unknown task %s", event.kind, task_id)
            return
        if task.is_terminal():
            logger.info(
                "Ignoring %s for terminal task %s (state=%s)",
                event.kind, task_id, task.state,
            )
            return

        if event.kind == RunEventKind.ACCEPTED:
            if task.state == HermesTaskState.PENDING:
                self._set_state(task, HermesTaskState.EXECUTING)
        elif event.kind == RunEventKind.COMPLETED:
            self._apply_terminal(task, HermesTaskState.COMPLETED, event.result_text)
        elif event.kind == RunEventKind.FAILED:
            self._apply_terminal(task, HermesTaskState.FAILED, None)
        elif event.kind == RunEventKind.CANCELLED:
            self._apply_terminal(task, HermesTaskState.CANCELLED, None)
        elif event.kind == RunEventKind.APPROVAL_REQUIRED:
            self._enqueue_approval(task, event)
        else:
            # Progress event. If a live turn is streaming this run, forward the
            # incremental assistant text (message.delta) into its sink for TTS.
            if event.payload.get("event") == "message.delta":
                sink = self._live_sinks.get(task_id)
                delta = event.payload.get("delta") or ""
                if sink is not None and delta:
                    self._delta_streamed.add(task_id)
                    sink.put_nowait(delta)
            logger.debug("Task %s progress event: %s", task_id, event.kind)

    def _apply_terminal(
        self, task: HermesTask, state: HermesTaskState, result_text: Optional[str]
    ) -> None:
        if task.is_terminal():
            # Late terminal event (e.g. result for a cancelled task): discard.
            logger.info(
                "Discarding late %s for %s (already %s)",
                state, task.task_id, task.state,
            )
            return
        self._set_state(task, state, result_text)

        sink = self._live_sinks.get(task.task_id)
        if sink is not None:
            # A live turn is streaming this run: finish its stream and suppress
            # the proactive copy (the current turn speaks the result live).
            if state == HermesTaskState.COMPLETED:
                # Runs that emit only a terminal result (no deltas, OQ-1
                # fallback) still need their text handed to the live turn.
                if task.task_id not in self._delta_streamed and result_text:
                    sink.put_nowait(result_text)
                task.delivered = True
            elif state == HermesTaskState.FAILED:
                sink.put_nowait(_FAILURE_SPEECH)
            # cancelled: nothing to say
            sink.put_nowait(self.LIVE_DONE)
            self._delta_streamed.discard(task.task_id)
            return

        self._delta_streamed.discard(task.task_id)
        if state == HermesTaskState.COMPLETED and result_text:
            self.delivery.enqueue(ProactiveResult(
                task_id=task.task_id,
                kind=DeliveryKind.RESULT,
                preamble=make_preamble(task.request_text),
                speech_text=result_text,
                full_text=result_text,
            ))
        elif state == HermesTaskState.FAILED:
            self._enqueue_notice(task, _FAILURE_SPEECH)
        # cancelled: result discarded, nothing delivered

    def _enqueue_notice(self, task: HermesTask, speech: str) -> None:
        self.delivery.enqueue(ProactiveResult(
            task_id=task.task_id,
            kind=DeliveryKind.FAILURE_NOTICE,
            preamble=make_preamble(task.request_text),
            speech_text=speech,
            full_text=speech,
        ))

    def _enqueue_approval(self, task: HermesTask, event: RunEvent) -> None:
        prompt = (
            "The agent needs your approval to continue. Say yes to allow or "
            "no to deny."
        )
        self.delivery.enqueue(ProactiveResult(
            task_id=task.task_id,
            kind=DeliveryKind.APPROVAL_REQUEST,
            preamble=make_preamble(task.request_text),
            speech_text=prompt,
            full_text=prompt,
        ))

    def _set_state(
        self,
        task: HermesTask,
        state: HermesTaskState,
        result_text: Optional[str] = None,
    ) -> None:
        task.state = state
        task.updated_at = time.time()
        if result_text is not None:
            task.result_text = result_text
        if self.transcript_manager is not None:
            self.transcript_manager.tasks[task.task_id] = task
            self.transcript_manager.update_state(task.task_id, state, result_text)

    def _on_delivered(self, item: ProactiveResult) -> None:
        task = self.tasks.get(item.task_id)
        if task is not None and item.kind == DeliveryKind.RESULT:
            task.delivered = True
