"""
tests/unit/test_stop.py — STOP/SYSTEM stage + RouterStage cancellation (US5 / T233).
"""

import unittest
from unittest import IsolatedAsyncioTestCase

from vocascade.pipeline.pipeline import PipelineStage, TranscriptionFrame, TextFrame
from vocascade.pipeline.router import RouterStage
from vocascade.session.state import SessionState, ConverseClaim
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
        registry.clear()   # no skills → the hermes fallback resolves to nothing

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
        # A non-stop, non-farewell utterance falls through (no skill here → nothing spoken).
        session = SessionState(voice_session_id="s1")
        sink = await _route("set a timer", session, broker=_FakeBroker([]))
        self.assertFalse(session.teardown_armed)
        self.assertEqual(_spoken(sink), [])


if __name__ == "__main__":
    unittest.main()
