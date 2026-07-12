"""
tests/unit/test_stop.py — STOP/SYSTEM stage + RouterStage cancellation (US5 / T233).
"""

import json
import asyncio
import unittest
from unittest import IsolatedAsyncioTestCase

from vocascade.adapter import _handle_control
from vocascade.delivery import DeliveryCoordinator
from vocascade.pipeline.pipeline import PipelineStage, TranscriptionFrame, TextFrame
from vocascade.pipeline.router import RouterStage
from vocascade.session.state import SessionState, ConverseClaim
from vocascade.session.state_machine import SessionMachine
from vocascade.skills.registry import registry
from vocascade.waterfall.router import WaterfallRouter
from vocascade.waterfall.stages.stop import StopStage


class _Cfg:
    skills_config = {}
    tts_character_name = "default"
    llm_base_url = ""
    llm_api_key = None
    llm_model = "x"
    audio_out_sample_rate = 32000


class _Sink(PipelineStage):
    def __init__(self):
        super().__init__()
        self.frames = []

    async def push(self, frame):
        self.frames.append(frame)


class _Task:
    def __init__(self, task_id, session_id):
        self.task_id = task_id
        self.session_id = session_id


class _FakeBroker:
    def __init__(self, tasks):
        self._tasks = tasks
        self.cancelled = []

    def active_tasks(self):
        return list(self._tasks)

    async def cancel(self, task_id):
        self.cancelled.append(task_id)
        return True


async def _route(utterance, session, broker=None):
    router = WaterfallRouter([StopStage(name="stop", threshold=0.5)], {})
    rs = RouterStage(router, session, _Cfg(), task_broker=broker, latency=None)
    sink = _Sink()
    rs.next_stage = sink
    await rs.push(TranscriptionFrame(text=utterance))
    return sink


def _spoken(sink):
    return [f.text for f in sink.frames if isinstance(f, TextFrame)]


class TestStopStage(IsolatedAsyncioTestCase):
    async def test_stop_classified(self):
        r = await StopStage(name="stop", threshold=0.5).evaluate("stop", None)
        self.assertEqual(r.confidence, 1.0)
        self.assertEqual(r.payload["action"], "stop")

    async def test_farewell_classified(self):
        r = await StopStage(name="stop").evaluate("that will be all", None)
        self.assertEqual(r.payload["action"], "farewell")

    async def test_normal_utterance_ignored(self):
        r = await StopStage(name="stop").evaluate("what is the weather", None)
        self.assertEqual(r.confidence, 0.0)


class TestRouterStop(IsolatedAsyncioTestCase):
    def setUp(self):
        registry.clear()
        # The stop handler is a real skill now (T236); register just it.
        import importlib
        import sys
        mod = "vocascade.skills.base_skills.stop"
        importlib.reload(sys.modules[mod]) if mod in sys.modules else importlib.import_module(mod)

    def tearDown(self):
        registry.clear()

    async def test_stop_cancels_session_runs_and_acks(self):
        broker = _FakeBroker([_Task("t1", "s1"), _Task("t2", "other-session")])
        session = SessionState(voice_session_id="s1")
        sink = await _route("stop", session, broker=broker)
        # Only this session's in-flight run is cancelled.
        self.assertEqual(broker.cancelled, ["t1"])
        self.assertEqual(_spoken(sink), ["Okay."])

    async def test_stop_releases_converse_claim(self):
        async def _resume(u, ctx):
            return "x"
        session = SessionState(voice_session_id="s1")
        session.converse_claim = ConverseClaim("timers", "for how long?", 0.0, _resume)
        await _route("cancel", session, broker=_FakeBroker([]))
        self.assertIsNone(session.converse_claim)

    async def test_farewell_arms_teardown_and_says_bye(self):
        session = SessionState(voice_session_id="s1")
        sink = await _route("goodbye", session)
        self.assertTrue(session.teardown_armed)
        self.assertEqual(_spoken(sink), ["Goodbye."])

    async def test_normal_utterance_is_not_system(self):
        # A non-stop, non-farewell utterance falls through. With no agent stage
        # in this fixture the waterfall exhausts, which now speaks a can't-help
        # notice (D6) instead of staying silent — but STOP must not intercept.
        session = SessionState(voice_session_id="s1")
        sink = await _route("set a timer", session, broker=_FakeBroker([]))
        self.assertFalse(session.teardown_armed)
        spoken = _spoken(sink)
        self.assertEqual(len(spoken), 1)
        self.assertIn("can't help", spoken[0])


class _FakePipeline:
    def __init__(self, sid):
        self.interrupt_event = asyncio.Event()
        self.session_machine = SessionMachine(SessionState(voice_session_id=sid))

    async def push(self, frame):
        pass


class _FakeMasker:
    filler_engine = None


class TestBargeInCancelsRuns(IsolatedAsyncioTestCase):
    async def _control(self, msg_type, broker, sid="s1"):
        async def _inject(text):
            pass

        async def _inject_audio(pcm):
            pass

        await _handle_control(
            json.dumps({"type": msg_type}),
            _FakePipeline(sid), asyncio.Queue(), DeliveryCoordinator(),
            _inject, _inject_audio, _FakeMasker(), broker,
        )

    async def test_interrupt_cancels_this_sessions_runs(self):
        # "scratch that, do X instead": a barge-in must abandon the in-flight run
        # so its result isn't pushed proactively later.
        broker = _FakeBroker([_Task("t1", "s1"), _Task("t2", "other-session")])
        await self._control("interrupt", broker)
        self.assertEqual(broker.cancelled, ["t1"])

    async def test_wakeword_does_not_cancel_runs(self):
        broker = _FakeBroker([_Task("t1", "s1")])
        await self._control("wakeword", broker)
        self.assertEqual(broker.cancelled, [])


if __name__ == "__main__":
    unittest.main()
