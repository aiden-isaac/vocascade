"""Unit tests for voice_adapter.task_broker (spec 005 T109)."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from voice_adapter.delivery import DeliveryCoordinator, DeliveryKind
from voice_adapter.hermes_run_client import (
    Capabilities,
    RunDispatchError,
    RunEvent,
    RunEventKind,
    RunHandle,
)
from voice_adapter.task_broker import TaskBroker
from voice_adapter.transcript_manager import HermesTaskState, TranscriptManager

CAPS_RUNS = Capabilities(supports_runs=True)
CAPS_FALLBACK = Capabilities(supports_runs=False)


def make_run_client(caps=CAPS_RUNS, events=None, dispatch_error=False):
    client = MagicMock()
    client.probe_capabilities = AsyncMock(return_value=caps)
    if dispatch_error:
        client.start_run = AsyncMock(side_effect=RunDispatchError("unreachable"))
    else:
        client.start_run = AsyncMock(
            return_value=RunHandle(run_id="run_1", status="started")
        )
    client.stop_run = AsyncMock(return_value={"status": "stopping"})

    async def stream_events(run_id, session_id=""):
        for e in (events or []):
            yield e

    client.stream_events = stream_events
    return client


def make_delivery() -> DeliveryCoordinator:
    # Unbound coordinator: enqueues are retained, nothing is spoken.
    return DeliveryCoordinator()


def ev(kind, run_id="run_1", **payload):
    payload.setdefault("run_id", run_id)
    return RunEvent(run_id=run_id, kind=kind, payload=payload)


class TestDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_creates_pending_then_executing(self):
        client = make_run_client(events=[])
        broker = TaskBroker(client, make_delivery(), TranscriptManager())
        task = await broker.dispatch("check my email", session_id="sess-1")
        self.assertEqual(task.state, HermesTaskState.EXECUTING)
        self.assertEqual(task.run_id, "run_1")
        self.assertEqual(task.request_text, "check my email")
        self.assertEqual(task.session_id, "sess-1")
        self.assertIn(task.task_id, broker.tasks)
        self.assertIs(broker.get_task_by_run("run_1"), task)
        await broker.shutdown()

    async def test_completion_hands_result_to_delivery(self):
        events = [
            ev(RunEventKind.ACCEPTED),
            ev(RunEventKind.PROGRESS, delta="..."),
            ev(RunEventKind.COMPLETED, output="3 meetings today"),
        ]
        client = make_run_client(events=events)
        delivery = make_delivery()
        broker = TaskBroker(client, delivery, TranscriptManager())
        task = await broker.dispatch("what's on my schedule")
        await asyncio.sleep(0)  # let the consumer drain
        self.assertEqual(task.state, HermesTaskState.COMPLETED)
        self.assertEqual(task.result_text, "3 meetings today")
        queued = delivery.pending()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].kind, DeliveryKind.RESULT)
        self.assertEqual(queued[0].speech_text, "3 meetings today")
        self.assertIn("schedule", queued[0].preamble)

    async def test_dispatch_failure_marks_failed_and_queues_notice(self):
        client = make_run_client(dispatch_error=True)
        delivery = make_delivery()
        broker = TaskBroker(client, delivery, TranscriptManager())
        task = await broker.dispatch("do a thing")
        self.assertEqual(task.state, HermesTaskState.FAILED)
        queued = delivery.pending()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].kind, DeliveryKind.FAILURE_NOTICE)

    async def test_failed_run_queues_failure_notice(self):
        events = [ev(RunEventKind.ACCEPTED), ev(RunEventKind.FAILED, error="boom")]
        client = make_run_client(events=events)
        delivery = make_delivery()
        broker = TaskBroker(client, delivery, TranscriptManager())
        task = await broker.dispatch("do a thing")
        await asyncio.sleep(0)
        self.assertEqual(task.state, HermesTaskState.FAILED)
        self.assertEqual(delivery.pending()[0].kind, DeliveryKind.FAILURE_NOTICE)


class TestIdempotentStateMachine(unittest.IsolatedAsyncioTestCase):
    async def _executing_task(self, delivery=None):
        client = make_run_client(events=[])
        broker = TaskBroker(client, delivery or make_delivery(), TranscriptManager())
        task = await broker.dispatch("req")
        await asyncio.sleep(0)
        return broker, task

    async def test_duplicate_terminal_events_no_op(self):
        delivery = make_delivery()
        broker, task = await self._executing_task(delivery)
        broker.apply_event(task.task_id, ev(RunEventKind.COMPLETED, output="first"))
        broker.apply_event(task.task_id, ev(RunEventKind.COMPLETED, output="second"))
        self.assertEqual(task.result_text, "first")
        self.assertEqual(len(delivery.pending()), 1)

    async def test_out_of_order_accepted_after_terminal_ignored(self):
        broker, task = await self._executing_task()
        broker.apply_event(task.task_id, ev(RunEventKind.COMPLETED, output="done"))
        broker.apply_event(task.task_id, ev(RunEventKind.ACCEPTED))
        self.assertEqual(task.state, HermesTaskState.COMPLETED)

    async def test_late_result_for_cancelled_task_discarded(self):
        delivery = make_delivery()
        broker, task = await self._executing_task(delivery)
        await broker.cancel(task.task_id)
        self.assertEqual(task.state, HermesTaskState.CANCELLED)
        broker.apply_event(task.task_id, ev(RunEventKind.COMPLETED, output="late"))
        self.assertEqual(task.state, HermesTaskState.CANCELLED)
        self.assertIsNone(task.result_text)
        self.assertEqual(delivery.pending(), [])

    async def test_unknown_task_event_no_crash(self):
        broker, _ = await self._executing_task()
        broker.apply_event("task_nope", ev(RunEventKind.COMPLETED, output="x"))

    async def test_transcript_manager_mirrors_state(self):
        tm = TranscriptManager()
        client = make_run_client(events=[])
        broker = TaskBroker(client, make_delivery(), tm)
        task = await broker.dispatch("req")
        self.assertIn(task.task_id, tm.tasks)
        broker.apply_event(task.task_id, ev(RunEventKind.COMPLETED, output="done"))
        self.assertEqual(tm.tasks[task.task_id].state, HermesTaskState.COMPLETED)


class TestCancel(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_issues_stop_and_is_optimistic(self):
        client = make_run_client(events=[])
        broker = TaskBroker(client, make_delivery(), TranscriptManager())
        task = await broker.dispatch("long job")
        ok = await broker.cancel(task.task_id)
        self.assertTrue(ok)
        self.assertEqual(task.state, HermesTaskState.CANCELLED)
        client.stop_run.assert_awaited_once_with("run_1")

    async def test_cancel_terminal_task_refused(self):
        client = make_run_client(events=[ev(RunEventKind.COMPLETED, output="done")])
        broker = TaskBroker(client, make_delivery(), TranscriptManager())
        task = await broker.dispatch("quick job")
        await asyncio.sleep(0)
        ok = await broker.cancel(task.task_id)
        self.assertFalse(ok)


class TestFallbackMode(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_synthesizes_one_completion(self):
        client = make_run_client(caps=CAPS_FALLBACK)

        async def chat_fallback(prompt, session_id=""):
            for chunk in ["Hello", " from", " chat"]:
                yield chunk

        client.chat_fallback = chat_fallback
        delivery = make_delivery()
        broker = TaskBroker(client, delivery, TranscriptManager())
        task = await broker.dispatch("say hello")
        await asyncio.sleep(0.01)
        self.assertEqual(task.state, HermesTaskState.COMPLETED)
        self.assertEqual(task.result_text, "Hello from chat")
        self.assertIsNone(task.run_id)  # no server run in fallback mode
        queued = delivery.pending()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].speech_text, "Hello from chat")
        client.start_run.assert_not_awaited()

    async def test_fallback_empty_stream_fails_task(self):
        client = make_run_client(caps=CAPS_FALLBACK)

        async def chat_fallback(prompt, session_id=""):
            return
            yield  # pragma: no cover

        client.chat_fallback = chat_fallback
        delivery = make_delivery()
        broker = TaskBroker(client, delivery, TranscriptManager())
        task = await broker.dispatch("say nothing")
        await asyncio.sleep(0.01)
        self.assertEqual(task.state, HermesTaskState.FAILED)
        self.assertEqual(delivery.pending()[0].kind, DeliveryKind.FAILURE_NOTICE)


if __name__ == "__main__":
    unittest.main()
