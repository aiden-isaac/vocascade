"""
tests/unit/test_hermes_stage.py — HERMES stage streamed + proactive delivery (US3 / T225).

Exercises the broker live-sink layer and the stage's streaming generator with a
fake run client: message.delta → batched sentences into TTS, terminal-only runs
(the OQ-1 / snapshot-reconcile shape) still delivering their output, proactive
fallback when no live turn is attached, and in-flight tasks surviving a session
end (FR-061).
"""

import asyncio
import unittest
from unittest import IsolatedAsyncioTestCase

from vocascade.hermes_run_client import RunEvent, RunEventKind, RunHandle, Capabilities
from vocascade.delivery import DeliveryCoordinator
from vocascade.task_broker import TaskBroker
from vocascade.waterfall.stages.hermes import stream_hermes_reply, _drain_sentences
from vocascade.skills.context import SkillContext, ToolBag
from vocascade.session.state import SessionState

_RID = "run_1"


def _delta(text):
    return RunEvent(run_id=_RID, kind=RunEventKind.PROGRESS,
                    payload={"event": "message.delta", "delta": text})


def _completed(output):
    return RunEvent(run_id=_RID, kind=RunEventKind.COMPLETED,
                    payload={"event": "run.completed", "output": output})


def _failed():
    return RunEvent(run_id=_RID, kind=RunEventKind.FAILED, payload={"event": "run.failed"})


class FakeRunClient:
    """Emits a fixed list of run events for one run."""

    def __init__(self, events):
        self.events = events

    async def probe_capabilities(self, force=False):
        return Capabilities(supports_runs=True)

    async def start_run(self, prompt, *, session_id=""):
        return RunHandle(run_id=_RID, status="running")

    async def stream_events(self, run_id, *, session_id=""):
        for ev in self.events:
            yield ev

    async def aclose(self):
        pass


class GatedRunClient:
    """Blocks the run until `gate` is set, then completes with `output`."""

    def __init__(self, gate, output):
        self.gate = gate
        self.output = output

    async def probe_capabilities(self, force=False):
        return Capabilities(supports_runs=True)

    async def start_run(self, prompt, *, session_id=""):
        return RunHandle(run_id=_RID, status="running")

    async def stream_events(self, run_id, *, session_id=""):
        await self.gate.wait()
        yield _completed(self.output)


def _broker(run_client, delivery=None):
    return TaskBroker(run_client, delivery or DeliveryCoordinator())


class TestDrainSentences(unittest.TestCase):
    def test_holds_incomplete_tail(self):
        complete, rem = _drain_sentences("The weather is sunny. Enjoy")
        self.assertEqual(complete, ["The weather is sunny."])
        self.assertEqual(rem, "Enjoy")

    def test_no_boundary_keeps_buffer(self):
        complete, rem = _drain_sentences("still going")
        self.assertEqual(complete, [])
        self.assertEqual(rem, "still going")


class TestHermesStreaming(IsolatedAsyncioTestCase):
    async def test_streams_deltas_as_sentences(self):
        broker = _broker(FakeRunClient([
            _delta("The weather "), _delta("is sunny. "),
            _delta("Enjoy "), _delta("your day."),
            _completed("The weather is sunny. Enjoy your day."),
        ]))
        out = [s async for s in stream_hermes_reply("weather?", broker, session_id="s1")]
        self.assertEqual(out, ["The weather is sunny.", "Enjoy your day."])

    async def test_terminal_only_run_delivers_output(self):
        # No deltas (OQ-1 fallback / snapshot-reconciled run): the full output
        # is still streamed into the live turn.
        broker = _broker(FakeRunClient([_completed("Here is the answer.")]))
        out = [s async for s in stream_hermes_reply("q", broker)]
        self.assertEqual(out, ["Here is the answer."])

    async def test_live_stream_suppresses_proactive(self):
        delivery = DeliveryCoordinator()
        broker = _broker(FakeRunClient([_delta("Done. "), _completed("Done.")]), delivery)
        out = [s async for s in stream_hermes_reply("q", broker)]
        self.assertEqual(out, ["Done."])
        # Spoken live → nothing queued for proactive delivery, task marked delivered.
        self.assertEqual(delivery.pending(), [])
        task = broker.active_tasks()
        self.assertEqual(task, [])  # terminal

    async def test_failed_run_speaks_notice_live(self):
        broker = _broker(FakeRunClient([_delta("working "), _failed()]))
        out = [s async for s in stream_hermes_reply("q", broker)]
        self.assertIn("wasn't able to finish", " ".join(out))


class TestProactiveAndRetention(IsolatedAsyncioTestCase):
    async def test_no_live_sink_delivers_proactively(self):
        # Conversation moved on (no live turn attached): completion is queued for
        # the delivery coordinator.
        delivery = DeliveryCoordinator()
        broker = _broker(FakeRunClient([_completed("Background result.")]), delivery)
        await broker.dispatch("q", session_id="s")
        await asyncio.sleep(0.05)  # let the consumer drain
        self.assertTrue(any(p.full_text == "Background result." for p in delivery.pending()))

    async def test_inflight_task_retained_across_session_end(self):
        delivery = DeliveryCoordinator()
        gate = asyncio.Event()
        broker = _broker(GatedRunClient(gate, "Late result."), delivery)
        task = await broker.dispatch("q")
        await asyncio.sleep(0.02)
        self.assertIn(task, broker.active_tasks())  # still running

        delivery.unbind_session()  # session ended — broker is NOT shut down
        self.assertIn(task, broker.active_tasks())  # retained (FR-061)

        gate.set()
        await asyncio.sleep(0.02)
        self.assertTrue(task.is_terminal())  # completes after the session ended


class TestHermesSkill(IsolatedAsyncioTestCase):
    async def test_skill_degrades_without_broker(self):
        from vocascade.skills.base_skills.hermes import handle_hermes
        ctx = SkillContext(tools=ToolBag(), session=SessionState(), task_broker=None)
        out = [s async for s in handle_hermes("q", {}, ctx)]
        self.assertEqual(out, ["I can't reach the agent right now."])

    async def test_skill_streams_with_broker(self):
        from vocascade.skills.base_skills.hermes import handle_hermes
        broker = _broker(FakeRunClient([_delta("Hi there."), _completed("Hi there.")]))
        ctx = SkillContext(tools=ToolBag(), session=SessionState(voice_session_id="s1"),
                           task_broker=broker)
        out = [s async for s in handle_hermes("q", {}, ctx)]
        self.assertEqual(out, ["Hi there."])


if __name__ == "__main__":
    unittest.main()
