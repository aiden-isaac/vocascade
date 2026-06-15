"""
tests/unit/test_session_summary.py — Session-end memory gist (US10 / T247).

FR-090: a concise gist of the session's turns is generated and POSTed to the
memory service when enabled. FR-091: generation/POST failures are logged and
non-blocking — they return falsy and never raise, so teardown is never blocked.
Also covers turn recording on SessionState and the RouterStage capturing the
spoken reply for the gist.
"""

import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from vocascade.session.summary import SessionSummarizer
from vocascade.session.state import SessionState
from vocascade.pipeline.pipeline import PipelineStage, TranscriptionFrame, TextFrame
from vocascade.pipeline.router import RouterStage
from vocascade.skills.registry import registry
from vocascade.waterfall.router import WaterfallRouter
from vocascade.waterfall.stages.high import HighStage


class _GistLLM:
    def __init__(self, gist="The user chatted and asked the time."):
        self.gist = gist
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.gist


class _FailLLM:
    async def chat(self, messages, **kwargs):
        raise RuntimeError("local LLM down")


def _turns():
    return [
        {"user": "how are you", "assistant": "Doing well, thanks.", "stage": "smalltalk"},
        {"user": "what time is it", "assistant": "It's 3 PM.", "stage": "high"},
    ]


class _CapturePost:
    """Async httpx.AsyncClient stand-in that records the posted JSON."""
    instances = []

    def __init__(self, *a, **k):
        self.posted = None
        _CapturePost.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.posted = (url, json)
        return MagicMock(raise_for_status=lambda: None)


# --- generation -------------------------------------------------------------

class TestSummaryGeneration(IsolatedAsyncioTestCase):
    async def test_gist_built_from_turns(self):
        s = SessionSummarizer("http://mem/x", _GistLLM("Quick chat about the time."))
        gist = await s.summarize(_turns())
        self.assertEqual(gist, "Quick chat about the time.")

    async def test_no_turns_no_gist(self):
        s = SessionSummarizer("http://mem/x", _GistLLM())
        self.assertIsNone(await s.summarize([]))

    async def test_llm_failure_is_non_blocking(self):
        s = SessionSummarizer("http://mem/x", _FailLLM())
        self.assertIsNone(await s.summarize(_turns()))   # logged, returns None, no raise


# --- enablement + send ------------------------------------------------------

class TestSummarySend(IsolatedAsyncioTestCase):
    def setUp(self):
        _CapturePost.instances = []

    async def test_disabled_when_no_url(self):
        s = SessionSummarizer("", _GistLLM())
        self.assertFalse(s.enabled)
        self.assertFalse(await s.summarize_and_send(_turns(), session_id="s1"))

    async def test_posts_gist_on_teardown(self):
        s = SessionSummarizer("http://mem/ingest", _GistLLM("Session gist."), peer="voice")
        with patch("vocascade.session.summary.httpx.AsyncClient", _CapturePost):
            ok = await s.summarize_and_send(_turns(), session_id="sess-1")
        self.assertTrue(ok)
        url, body = _CapturePost.instances[0].posted
        self.assertEqual(url, "http://mem/ingest")
        self.assertEqual(body["content"], "Session gist.")
        self.assertEqual(body["session_id"], "sess-1")
        self.assertEqual(body["peer"], "voice")

    async def test_post_failure_is_non_blocking(self):
        class _BoomClient(_CapturePost):
            async def post(self, url, json=None):
                raise OSError("memory service unreachable")

        s = SessionSummarizer("http://mem/ingest", _GistLLM())
        with patch("vocascade.session.summary.httpx.AsyncClient", _BoomClient):
            ok = await s.summarize_and_send(_turns(), session_id="s1")   # must not raise
        self.assertFalse(ok)


# --- turn recording ---------------------------------------------------------

class TestTurnRecording(unittest.TestCase):
    def test_record_turn_filters_empty(self):
        st = SessionState(voice_session_id="s1")
        st.record_turn("hi", "hello", "smalltalk")
        st.record_turn("", "ignored", "x")
        st.record_turn("orphan", "", "x")
        self.assertEqual(st.turns, [{"user": "hi", "assistant": "hello", "stage": "smalltalk"}])


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


class TestRouterRecordsTurns(IsolatedAsyncioTestCase):
    def setUp(self):
        registry.clear()

    def tearDown(self):
        registry.clear()

    async def test_spoken_reply_recorded_on_session(self):
        async def handler(intent, entities, ctx):
            return "It's 3 PM."

        registry.register(name="datetime", handler=handler, keywords=["time"])
        session = SessionState(voice_session_id="s1")
        router = WaterfallRouter([HighStage(name="high", threshold=0.95)], {})
        rs = RouterStage(router, session, _Cfg(), latency=None)
        rs.next_stage = _Sink()

        await rs.push(TranscriptionFrame(text="what time is it"))
        self.assertEqual(len(session.turns), 1)
        self.assertEqual(session.turns[0]["user"], "what time is it")
        self.assertEqual(session.turns[0]["assistant"], "It's 3 PM.")
        self.assertEqual(session.turns[0]["stage"], "high")


if __name__ == "__main__":
    unittest.main()
