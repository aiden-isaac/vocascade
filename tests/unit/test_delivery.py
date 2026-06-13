"""Unit tests for vocascade.delivery — MVP subset (spec 005 T110):
idle gate, FIFO order, preamble derivation."""

import asyncio
import unittest

from vocascade.delivery import (
    DeliveryCoordinator,
    DeliveryKind,
    DeliveryState,
    ProactiveResult,
    make_preamble,
)


def result(task_id="task_1", text="the answer", request="what's on my schedule today?"):
    return ProactiveResult(
        task_id=task_id,
        kind=DeliveryKind.RESULT,
        preamble=make_preamble(request),
        speech_text=text,
        full_text=text,
    )


class Sink:
    """Records injected speech and lets tests simulate bot speech lifecycle."""

    def __init__(self, coordinator: DeliveryCoordinator):
        self.coordinator = coordinator
        self.spoken: list[str] = []

    async def inject(self, text: str) -> None:
        self.spoken.append(text)

    async def finish_bot_speech(self):
        """Simulate TTS finishing the injected speech."""
        self.coordinator.notify_bot_started_speaking()
        self.coordinator.notify_bot_stopped_speaking()
        await asyncio.sleep(0)


class TestMakePreamble(unittest.TestCase):
    def test_derived_from_request_text(self):
        p = make_preamble("what's on my schedule today?")
        self.assertIn("schedule", p)
        self.assertTrue(p.startswith("About your request"))
        self.assertTrue(p.endswith(": "))

    def test_long_request_truncated(self):
        p = make_preamble("please go and find out " + "x " * 30)
        self.assertLess(len(p), 80)
        self.assertIn("…", p)

    def test_empty_request(self):
        self.assertEqual(make_preamble("   "), "About your earlier request: ")


class TestIdleGate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dc = DeliveryCoordinator()
        self.sink = Sink(self.dc)

    async def test_nothing_spoken_without_session(self):
        self.dc.enqueue(result())
        await asyncio.sleep(0.01)
        self.assertEqual(self.sink.spoken, [])
        self.assertEqual(len(self.dc.pending()), 1)

    async def test_delivers_when_idle_session_bound(self):
        self.dc.bind_session(self.sink.inject)
        self.dc.enqueue(result(text="3 meetings"))
        await asyncio.sleep(0.01)
        self.assertEqual(len(self.sink.spoken), 1)
        self.assertIn("3 meetings", self.sink.spoken[0])
        self.assertTrue(self.sink.spoken[0].startswith("About your request"))

    async def test_deferred_while_user_speaking(self):
        self.dc.bind_session(self.sink.inject)
        self.dc.notify_user_started_speaking()
        self.dc.enqueue(result())
        await asyncio.sleep(0.01)
        self.assertEqual(self.sink.spoken, [])
        # User finishes and the transcription reaches the LLM => still busy
        self.dc.notify_user_stopped_speaking()
        self.dc.notify_response_pending()
        await asyncio.sleep(0.01)
        self.assertEqual(self.sink.spoken, [])
        # Bot answers and finishes => idle => delivery fires
        self.dc.notify_bot_started_speaking()
        self.dc.notify_bot_stopped_speaking()
        await asyncio.sleep(0.01)
        self.assertEqual(len(self.sink.spoken), 1)

    async def test_vad_stop_without_transcription_does_not_block(self):
        # Noise triggers VAD start/stop but produces no transcription; the
        # channel must return to idle and deliver.
        self.dc.bind_session(self.sink.inject)
        self.dc.notify_user_started_speaking()
        self.dc.enqueue(result())
        self.dc.notify_user_stopped_speaking()
        await asyncio.sleep(0.01)
        self.assertEqual(len(self.sink.spoken), 1)

    async def test_deferred_while_bot_speaking(self):
        self.dc.bind_session(self.sink.inject)
        self.dc.notify_bot_started_speaking()
        self.dc.enqueue(result())
        await asyncio.sleep(0.01)
        self.assertEqual(self.sink.spoken, [])
        self.dc.notify_bot_stopped_speaking()
        await asyncio.sleep(0.01)
        self.assertEqual(len(self.sink.spoken), 1)


class TestFifoOrder(unittest.IsolatedAsyncioTestCase):
    async def test_results_delivered_in_enqueue_order(self):
        dc = DeliveryCoordinator()
        sink = Sink(dc)
        dc.bind_session(sink.inject)
        dc.notify_bot_started_speaking()  # hold the channel busy while queueing
        dc.enqueue(result(task_id="t1", text="first result", request="first thing"))
        dc.enqueue(result(task_id="t2", text="second result", request="second thing"))
        dc.notify_bot_stopped_speaking()
        await asyncio.sleep(0.01)
        # One at a time: the first is in flight, the second still queued.
        self.assertEqual(len(sink.spoken), 1)
        self.assertIn("first result", sink.spoken[0])
        self.assertEqual(len(dc.pending()), 1)
        await sink.finish_bot_speech()
        await asyncio.sleep(0.01)
        self.assertEqual(len(sink.spoken), 2)
        self.assertIn("second result", sink.spoken[1])

    async def test_delivered_state_and_callback(self):
        delivered = []
        dc = DeliveryCoordinator(on_delivered=delivered.append)
        sink = Sink(dc)
        dc.bind_session(sink.inject)
        item = result()
        dc.enqueue(item)
        await asyncio.sleep(0.01)
        self.assertEqual(item.state, DeliveryState.SPEAKING)
        await sink.finish_bot_speech()
        self.assertEqual(item.state, DeliveryState.DELIVERED)
        self.assertEqual(delivered, [item])


class TestInterruption(unittest.IsolatedAsyncioTestCase):
    async def test_interruption_kills_in_flight_without_latching_gate(self):
        dc = DeliveryCoordinator()
        sink = Sink(dc)
        dc.bind_session(sink.inject)
        item = result(task_id="t1", text="long result")
        dc.enqueue(item)
        await asyncio.sleep(0.01)
        self.assertEqual(item.state, DeliveryState.SPEAKING)
        # Client "interrupt" without any VAD framing: item dies, no re-queue,
        # and the gate must NOT latch user_speaking…
        dc.notify_interruption()
        self.assertEqual(item.state, DeliveryState.INTERRUPTED)
        # …so a later result still flows once the channel is quiet.
        dc.enqueue(result(task_id="t2", text="next result"))
        await asyncio.sleep(0.01)
        self.assertEqual(len(sink.spoken), 2)
        self.assertIn("next result", sink.spoken[1])


class TestSessionBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_queued_items_retained_across_unbind(self):
        dc = DeliveryCoordinator()
        sink = Sink(dc)
        dc.enqueue(result(text="held result"))
        self.assertEqual(len(dc.pending()), 1)
        # Next session binds => backlog delivered
        dc.bind_session(sink.inject)
        await asyncio.sleep(0.01)
        self.assertEqual(len(sink.spoken), 1)
        self.assertIn("held result", sink.spoken[0])


if __name__ == "__main__":
    unittest.main()
