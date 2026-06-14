"""
tests/unit/test_latency.py — Latency masking (US4 / T228).

Covers the filler policy (HIGH none / MEDIUM tool-clip / HERMES query-opening,
FR-041/042/043), the optimistic opening generator (T227), voice-optimization,
and the masker's emit behavior (instant clip preferred, opening fallback).
"""

import unittest
from unittest import TestCase, IsolatedAsyncioTestCase

from vocascade.pipeline.latency import (
    FillerPolicy,
    LatencyMasker,
    OptimisticOpening,
    optimize_for_voice,
    _GENERIC_OPENINGS,
)


class _FakeFiller:
    def __init__(self, clips):
        self.clips = clips  # {category: pcm}

    def get_filler(self, category):
        return self.clips.get(category)


class _FakeLLM:
    def __init__(self, response):
        self.response = response

    async def chat(self, messages, **kwargs):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _Capture:
    def __init__(self):
        self.clips = []
        self.texts = []

    async def emit_clip(self, pcm):
        self.clips.append(pcm)

    async def emit_text(self, text):
        self.texts.append(text)


class TestFillerPolicy(TestCase):
    def test_high_plays_nothing(self):
        self.assertEqual(FillerPolicy().decide("high", {}).kind, "none")

    def test_medium_is_tool_clip(self):
        d = FillerPolicy().decide("medium", {"filler": "thinking"})
        self.assertEqual((d.kind, d.category), ("clip", "thinking"))

    def test_hermes_is_query_opening(self):
        d = FillerPolicy().decide("hermes", {"filler": "working"})
        self.assertEqual((d.kind, d.category), ("opening", "working"))

    def test_smalltalk_plays_nothing(self):
        self.assertEqual(FillerPolicy().decide("smalltalk", {}).kind, "none")

    def test_explicit_none_disables(self):
        self.assertEqual(FillerPolicy().decide("hermes", {"filler": "none"}).kind, "none")
        self.assertEqual(FillerPolicy().decide("medium", {"filler": ""}).kind, "none")

    def test_default_categories(self):
        self.assertEqual(FillerPolicy().decide("medium", {}).category, "thinking")
        self.assertEqual(FillerPolicy().decide("hermes", {}).category, "working")


class TestOptimizeForVoice(TestCase):
    def test_strips_markdown(self):
        self.assertEqual(optimize_for_voice("**Hello** `world`"), "Hello world")

    def test_collapses_whitespace_and_quotes(self):
        self.assertEqual(optimize_for_voice('  "Let me   check."  '), "Let me check.")

    def test_caps_at_sentence_boundary(self):
        out = optimize_for_voice("Sentence one. Sentence two. Sentence three.", max_chars=20)
        self.assertLessEqual(len(out), 21)
        self.assertTrue(out.endswith(".") or out.endswith("…"))

    def test_empty(self):
        self.assertEqual(optimize_for_voice(""), "")


class TestOptimisticOpening(IsolatedAsyncioTestCase):
    async def test_uses_llm(self):
        out = await OptimisticOpening().generate("what's the weather", _FakeLLM("Let me check the weather."))
        self.assertEqual(out, "Let me check the weather.")

    async def test_llm_failure_falls_back(self):
        out = await OptimisticOpening().generate("q", _FakeLLM(RuntimeError("down")))
        self.assertIn(out, _GENERIC_OPENINGS)

    async def test_no_llm_uses_generic(self):
        out = await OptimisticOpening().generate("q", None)
        self.assertIn(out, _GENERIC_OPENINGS)


class TestLatencyMasker(IsolatedAsyncioTestCase):
    async def _mask(self, masker, stage, skill_config, llm=None):
        cap = _Capture()
        decision = await masker.mask(
            stage=stage, skill_config=skill_config, utterance="x",
            local_llm=llm, emit_clip=cap.emit_clip, emit_text=cap.emit_text,
        )
        return cap, decision

    async def test_high_emits_nothing(self):
        cap, d = await self._mask(LatencyMasker(_FakeFiller({})), "high", {})
        self.assertEqual((cap.clips, cap.texts), ([], []))
        self.assertEqual(d.kind, "none")

    async def test_medium_emits_clip_when_available(self):
        cap, _ = await self._mask(
            LatencyMasker(_FakeFiller({"thinking": b"PCM"})), "medium", {"filler": "thinking"})
        self.assertEqual(cap.clips, [b"PCM"])
        self.assertEqual(cap.texts, [])

    async def test_medium_without_clip_emits_nothing(self):
        cap, _ = await self._mask(
            LatencyMasker(_FakeFiller({})), "medium", {"filler": "thinking"})
        self.assertEqual((cap.clips, cap.texts), ([], []))

    async def test_hermes_speaks_opening_when_no_clip(self):
        cap, _ = await self._mask(
            LatencyMasker(_FakeFiller({})), "hermes", {"filler": "working"},
            llm=_FakeLLM("Let me check on that."))
        self.assertEqual(cap.texts, ["Let me check on that."])
        self.assertEqual(cap.clips, [])

    async def test_hermes_prefers_instant_clip(self):
        cap, _ = await self._mask(
            LatencyMasker(_FakeFiller({"working": b"WCLIP"})), "hermes", {"filler": "working"},
            llm=_FakeLLM("unused"))
        self.assertEqual(cap.clips, [b"WCLIP"])
        self.assertEqual(cap.texts, [])

    async def test_disabled_filler_emits_nothing(self):
        cap, _ = await self._mask(
            LatencyMasker(_FakeFiller({"working": b"WCLIP"})), "hermes", {"filler": "none"})
        self.assertEqual((cap.clips, cap.texts), ([], []))


if __name__ == "__main__":
    unittest.main()
