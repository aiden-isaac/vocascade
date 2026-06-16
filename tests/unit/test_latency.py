"""
tests/unit/test_latency.py — Latency masking (US4 / T228).

Covers the dynamic filler policy (HIGH none / MEDIUM + HERMES dynamic opening),
the configurable FillerProvider (pool / llm / hybrid), voice-optimization, and
the progressive interval-filler wrapper (emits follow-ups during the wait, stops
on first content, respects the cap, never cancels the underlying stream).
"""

import asyncio
import unittest
from unittest import TestCase, IsolatedAsyncioTestCase

from vocascade.pipeline.latency import (
    FillerPolicy,
    FillerProvider,
    LatencyMasker,
    optimize_for_voice,
    _POOL_OPENINGS,
    _POOL_FOLLOWUPS,
)


class _FakeLLM:
    def __init__(self, response):
        self.response = response

    async def chat(self, messages, **kwargs):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _Capture:
    def __init__(self):
        self.texts = []

    async def emit_text(self, text):
        self.texts.append(text)


# --- FillerPolicy -----------------------------------------------------------

class TestFillerPolicy(TestCase):
    def test_high_plays_nothing(self):
        self.assertEqual(FillerPolicy().decide("high", {}).kind, "none")

    def test_medium_and_hermes_open(self):
        self.assertEqual(FillerPolicy().decide("medium", {}).kind, "opening")
        self.assertEqual(FillerPolicy().decide("hermes", {"filler": "working"}).kind, "opening")

    def test_smalltalk_plays_nothing(self):
        self.assertEqual(FillerPolicy().decide("smalltalk", {}).kind, "none")

    def test_explicit_none_disables(self):
        self.assertEqual(FillerPolicy().decide("hermes", {"filler": "none"}).kind, "none")
        self.assertEqual(FillerPolicy().decide("medium", {"filler": ""}).kind, "none")


# --- FillerProvider modes ---------------------------------------------------

class TestFillerProvider(IsolatedAsyncioTestCase):
    async def test_pool_opening_and_followup(self):
        p = FillerProvider("pool")
        self.assertIn(await p.opening("q", _FakeLLM("ignored")), _POOL_OPENINGS)  # LLM unused
        self.assertEqual(await p.followup("q", None, 0), _POOL_FOLLOWUPS[0])
        self.assertEqual(await p.followup("q", None, 1), _POOL_FOLLOWUPS[1])

    async def test_pool_followup_clamps_at_last(self):
        p = FillerProvider("pool")
        self.assertEqual(await p.followup("q", None, 99), _POOL_FOLLOWUPS[-1])

    async def test_llm_mode_uses_llm_for_both(self):
        p = FillerProvider("llm")
        self.assertEqual(await p.opening("q", _FakeLLM("Checking that now.")), "Checking that now.")
        self.assertEqual(await p.followup("q", _FakeLLM("Still on it."), 0), "Still on it.")

    async def test_hybrid_llm_opening_pool_followup(self):
        p = FillerProvider("hybrid")
        self.assertEqual(await p.opening("q", _FakeLLM("Pulling that up.")), "Pulling that up.")
        # follow-ups come from the pool even in hybrid mode
        self.assertEqual(await p.followup("q", _FakeLLM("ignored"), 0), _POOL_FOLLOWUPS[0])

    async def test_llm_failure_falls_back_to_pool(self):
        p = FillerProvider("llm")
        self.assertIn(await p.opening("q", _FakeLLM(RuntimeError("down"))), _POOL_OPENINGS)
        self.assertEqual(await p.followup("q", _FakeLLM(RuntimeError("down")), 0), _POOL_FOLLOWUPS[0])

    async def test_no_llm_uses_pool(self):
        p = FillerProvider("hybrid")
        self.assertIn(await p.opening("q", None), _POOL_OPENINGS)


class TestOptimizeForVoice(TestCase):
    def test_strips_markdown_and_quotes(self):
        self.assertEqual(optimize_for_voice('  "**Let me** `check`."  '), "Let me check.")

    def test_caps_at_sentence_boundary(self):
        out = optimize_for_voice("Sentence one. Sentence two. Sentence three.", max_chars=20)
        self.assertLessEqual(len(out), 21)


# --- LatencyMasker.mask (opening, no pre-rendered clips) ---------------------

class TestMaskOpening(IsolatedAsyncioTestCase):
    async def _mask(self, masker, stage, skill_config, llm=None):
        cap = _Capture()
        d = await masker.mask(stage=stage, skill_config=skill_config, utterance="weather?",
                              local_llm=llm, emit_text=cap.emit_text)
        return cap, d

    async def test_high_emits_nothing(self):
        cap, d = await self._mask(LatencyMasker(provider=FillerProvider("pool")), "high", {})
        self.assertEqual(cap.texts, [])
        self.assertEqual(d.kind, "none")

    async def test_hermes_speaks_dynamic_opening(self):
        cap, _ = await self._mask(
            LatencyMasker(provider=FillerProvider("llm")), "hermes", {"filler": "working"},
            llm=_FakeLLM("Let me check on that."))
        self.assertEqual(cap.texts, ["Let me check on that."])

    async def test_medium_speaks_opening_from_pool(self):
        cap, _ = await self._mask(LatencyMasker(provider=FillerProvider("pool")), "medium", {})
        self.assertEqual(len(cap.texts), 1)
        self.assertIn(cap.texts[0], _POOL_OPENINGS)

    async def test_disabled_filler_silent(self):
        cap, _ = await self._mask(LatencyMasker(provider=FillerProvider("pool")),
                                  "hermes", {"filler": "none"})
        self.assertEqual(cap.texts, [])


# --- Progressive interval fillers -------------------------------------------

class TestProgressiveFillers(IsolatedAsyncioTestCase):
    def _masker(self, **kw):
        kw.setdefault("interval", 0.04)
        kw.setdefault("backoff", False)
        return LatencyMasker(provider=FillerProvider("pool"), **kw)

    async def test_emits_fillers_then_streams_content(self):
        async def stream():
            await asyncio.sleep(0.15)   # ~3 intervals of dead air
            yield "The answer."
        out = [x async for x in self._masker(max_fillers=5).with_progressive_fillers(stream(), "q", None)]
        fillers = [t for t, f in out if f]
        content = [t for t, f in out if not f]
        self.assertGreaterEqual(len(fillers), 1)        # at least one follow-up fired
        self.assertEqual(content, ["The answer."])      # content fully delivered

    async def test_respects_max_fillers(self):
        async def stream():
            await asyncio.sleep(0.3)    # long dead air
            yield "done."
        out = [x async for x in self._masker(max_fillers=2).with_progressive_fillers(stream(), "q", None)]
        self.assertEqual(len([t for t, f in out if f]), 2)   # capped at 2

    async def test_max_zero_disables(self):
        async def stream():
            await asyncio.sleep(0.1)
            yield "done."
        out = [x async for x in self._masker(max_fillers=0).with_progressive_fillers(stream(), "q", None)]
        self.assertEqual(out, [("done.", False)])

    async def test_stream_completes_fully_not_cancelled(self):
        async def stream():
            await asyncio.sleep(0.1)    # one filler, then content
            yield "first."
            yield "second."             # arrives immediately after — no filler between
        out = [x async for x in self._masker(max_fillers=3).with_progressive_fillers(stream(), "q", None)]
        self.assertEqual([t for t, f in out if not f], ["first.", "second."])

    async def test_opening_dispatches_before_filler_generates(self):
        """FR-043: the pump (which fires the run dispatch on first pull) must run
        BEFORE the opening filler is generated. The opening provider here blocks on
        an event the pump sets — so if it weren't parallel, this would deadlock."""
        pump_started = asyncio.Event()

        class Prov(FillerProvider):
            async def opening(self, utterance, local_llm):
                await asyncio.wait_for(pump_started.wait(), 1.0)  # proves pump ran first
                return "On it."

        async def stream():
            pump_started.set()          # the dispatch happens on this first pull
            yield "The answer."

        masker = LatencyMasker(provider=Prov("pool"), interval=0.04, backoff=False, max_fillers=3)
        out = [x async for x in masker.with_progressive_fillers(
            stream(), "q", None, opening=True)]
        self.assertEqual(out[0], ("On it.", True))          # opening first, as a filler
        self.assertIn(("The answer.", False), out)          # content delivered


if __name__ == "__main__":
    unittest.main()
