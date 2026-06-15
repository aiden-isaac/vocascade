"""
tests/unit/test_degradation.py — Graceful degradation at every stage (US7 / T240).

No failure is silent: a skill/tool exception speaks an error, a hung classifier
degrades fast and falls through, an unreachable Hermes queues a notice without
crashing the local loop, and smalltalk speaks a fallback when its LLM is down.
"""

import unittest
from unittest import IsolatedAsyncioTestCase, TestCase

from vocascade.pipeline.pipeline import PipelineStage, TranscriptionFrame, TextFrame
from vocascade.pipeline.router import RouterStage
from vocascade.skills.registry import registry
from vocascade.skills.context import SkillContext, ToolBag
from vocascade.session.state import SessionState
from vocascade.waterfall.router import WaterfallRouter
from vocascade.waterfall.stages.high import HighStage
from vocascade.waterfall.stages.medium import MediumStage
from vocascade.waterfall.classifier import IntentClassifier
from vocascade.delivery import DeliveryCoordinator
from vocascade.task_broker import TaskBroker
from vocascade.hermes_run_client import RunDispatchError, Capabilities


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


class _FailLLM:
    async def chat(self, messages, **kwargs):
        raise RuntimeError("local LLM unreachable")


async def _h(intent, entities, ctx):
    return "ok"


def _ctx():
    return SkillContext(tools=ToolBag(), session=SessionState(voice_session_id="s1"))


def _spoken(sink):
    return [f.text for f in sink.frames if isinstance(f, TextFrame)]


# --- FR-100: a skill/tool exception speaks a graceful error ------------------

class TestSkillFailureDegrades(IsolatedAsyncioTestCase):
    def setUp(self):
        registry.clear()

    def tearDown(self):
        registry.clear()

    async def test_handler_exception_speaks_error_not_silence(self):
        async def boom(intent, entities, ctx):
            raise RuntimeError("tool call failed")

        registry.register(name="boomer", handler=boom, keywords=["boom"])
        router = WaterfallRouter([HighStage(name="high", threshold=0.95)], {})
        rs = RouterStage(router, SessionState(voice_session_id="s1"), _Cfg(), latency=None)
        sink = _Sink()
        rs.next_stage = sink

        await rs.push(TranscriptionFrame(text="boom now"))
        self.assertEqual(_spoken(sink), ["Sorry, I ran into a problem with that."])


# --- FR-100: a hung classifier degrades fast and falls through ---------------

class TestClassifierDegrades(IsolatedAsyncioTestCase):
    def setUp(self):
        registry.clear()
        registry.register(name="tasks", handler=_h, examples=["what are my tasks"])

    def tearDown(self):
        registry.clear()

    async def test_classifier_failure_is_skipped(self):
        stage = MediumStage(classifier=IntentClassifier(), llm=_FailLLM())
        result = await stage.evaluate("anything", _ctx())
        self.assertEqual(result.confidence, 0.0)   # no crash; falls through

    def test_classifier_built_with_short_timeout(self):
        class C(_Cfg):
            waterfall_stages = ["medium", "smalltalk", "hermes"]
            waterfall_thresholds = {"high": 0.95, "medium": 0.65, "low": 0.35}
            skills_config = {}
            llm_base_url = "http://localhost:9/v1"   # set → classifier LLM built (no call)
            classifier_model = None
            classifier_max_examples = 5
            medium_band_low = 0.5
            medium_band_high = 0.8
            classifier_timeout_seconds = 3.0

        router = WaterfallRouter.from_config(C())
        medium = next(s for s in router.stages if s.name == "medium")
        self.assertEqual(medium.llm.timeout, 3.0)


# --- FR-101: Hermes unreachable → notice, local loop keeps working -----------

class _DispatchFailRunClient:
    async def probe_capabilities(self, force=False):
        return Capabilities(supports_runs=True)

    async def start_run(self, prompt, *, session_id=""):
        raise RunDispatchError("hermes unreachable")

    async def aclose(self):
        pass


class TestHermesUnreachable(IsolatedAsyncioTestCase):
    async def test_dispatch_failure_queues_notice_without_raising(self):
        delivery = DeliveryCoordinator()
        broker = TaskBroker(_DispatchFailRunClient(), delivery)
        task = await broker.dispatch("what's my schedule", session_id="s1")
        self.assertTrue(task.is_terminal())                 # failed, did not raise
        self.assertTrue(delivery.pending())                 # a spoken notice is queued

    async def test_hermes_skill_survives_dispatch_failure(self):
        from vocascade.skills.base_skills.hermes import handle_hermes
        broker = TaskBroker(_DispatchFailRunClient(), DeliveryCoordinator())
        ctx = SkillContext(tools=ToolBag(), session=SessionState(voice_session_id="s1"),
                           task_broker=broker)
        out = [chunk async for chunk in handle_hermes("q", {}, ctx)]
        self.assertEqual(out, [])   # no in-turn speech; proactive notice covers it, no crash


# --- smalltalk LLM down → spoken fallback ------------------------------------

class TestSmalltalkDegrades(IsolatedAsyncioTestCase):
    async def test_smalltalk_speaks_fallback_when_llm_down(self):
        from vocascade.skills.base_skills.smalltalk import handle_smalltalk
        ctx = SkillContext(tools=ToolBag(), session=SessionState(), local_llm=_FailLLM(),
                           config={"tts_character_name": "default"})
        out = await handle_smalltalk("how are you", {}, ctx)
        self.assertIn("trouble responding", out.lower())


if __name__ == "__main__":
    unittest.main()
