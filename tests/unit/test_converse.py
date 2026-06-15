"""
tests/unit/test_converse.py — CONVERSE multi-turn claim (US5 / T233).
"""

import time
import unittest
from unittest import IsolatedAsyncioTestCase

from vocascade.pipeline.pipeline import PipelineStage, TranscriptionFrame, TextFrame
from vocascade.pipeline.router import RouterStage
from vocascade.skills.context import SkillContext, ToolBag
from vocascade.skills.registry import registry
from vocascade.session.state import SessionState, ConverseClaim
from vocascade.waterfall.router import WaterfallRouter
from vocascade.waterfall.stages.converse import ConverseStage


async def _noop_resume(utterance, ctx):
    return "ok"


def _ctx(claim):
    session = SessionState(voice_session_id="s1")
    session.converse_claim = claim
    return SkillContext(tools=ToolBag(), session=session)


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


class TestConverseStage(IsolatedAsyncioTestCase):
    async def test_active_claim_wins(self):
        claim = ConverseClaim("timers", "for how long?", time.time() + 60, _noop_resume)
        r = await ConverseStage(name="converse", threshold=0.5).evaluate("five minutes", _ctx(claim))
        self.assertEqual(r.confidence, 1.0)
        self.assertEqual(r.skill_name, "timers")
        self.assertTrue(r.payload["converse"])

    async def test_expired_claim_released(self):
        claim = ConverseClaim("timers", "for how long?", time.time() - 1, _noop_resume)
        ctx = _ctx(claim)
        r = await ConverseStage(name="converse", threshold=0.5).evaluate("x", ctx)
        self.assertEqual(r.confidence, 0.0)
        self.assertIsNone(ctx.session.converse_claim)   # released on timeout

    async def test_no_claim(self):
        r = await ConverseStage(name="converse", threshold=0.5).evaluate("x", _ctx(None))
        self.assertEqual(r.confidence, 0.0)


class TestRouterConverse(IsolatedAsyncioTestCase):
    def setUp(self):
        registry.clear()

    def tearDown(self):
        registry.clear()

    async def test_converse_routes_to_resume_and_releases(self):
        captured = {}

        async def resume(utterance, ctx):
            captured["utterance"] = utterance
            return f"You said {utterance}"

        session = SessionState(voice_session_id="s1")
        session.converse_claim = ConverseClaim("timers", "for how long?", time.time() + 60, resume)
        router = WaterfallRouter([ConverseStage(name="converse", threshold=0.5)], {})
        rs = RouterStage(router, session, _Cfg(), latency=None)
        sink = _Sink()
        rs.next_stage = sink

        await rs.push(TranscriptionFrame(text="five minutes"))

        self.assertEqual(captured["utterance"], "five minutes")
        self.assertIsNone(session.converse_claim)   # released on consumption
        spoken = [f.text for f in sink.frames if isinstance(f, TextFrame)]
        self.assertEqual(spoken, ["You said five minutes"])


if __name__ == "__main__":
    unittest.main()
