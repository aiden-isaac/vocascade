"""
tests/unit/test_waterfall.py — Confidence-waterfall routing tests (US2 / T222).

Covers: HIGH keyword matching, the MEDIUM local-LLM classifier (clamping,
malformed-output resilience, prompt auto-generation/regeneration per SC-007),
and the router's ordered/threshold/tie-break resolution.
"""

import unittest
from unittest import IsolatedAsyncioTestCase, TestCase

from vocascade.skills.registry import registry
from vocascade.skills.context import SkillContext, ToolBag
from vocascade.session.state import SessionState
from vocascade.waterfall.classifier import IntentClassifier
from vocascade.waterfall.stages.high import HighStage
from vocascade.waterfall.stages.medium import MediumStage
from vocascade.waterfall.router import WaterfallRouter, StopStage, SmalltalkStage


async def _dummy(intent, entities, ctx):
    return "ok"


class _FakeLLM:
    """Returns a canned chat response (or raises, if given an Exception)."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _ctx(llm=None):
    return SkillContext(tools=ToolBag(), session=SessionState(), local_llm=llm)


class _RegistryIsolated:
    def setUp(self):
        registry.clear()

    def tearDown(self):
        registry.clear()


# --- HIGH stage (T219) ------------------------------------------------------

class TestHighStage(_RegistryIsolated, IsolatedAsyncioTestCase):
    async def test_keyword_match_wins(self):
        registry.register(name="timers", handler=_dummy, keywords=["timer", "set a timer"])
        res = await HighStage(threshold=0.95).evaluate("set a timer for five minutes", _ctx())
        self.assertEqual(res.skill_name, "timers")
        self.assertGreaterEqual(res.confidence, 0.95)

    async def test_no_match_is_zero(self):
        registry.register(name="timers", handler=_dummy, keywords=["timer"])
        res = await HighStage().evaluate("what is the weather today", _ctx())
        self.assertEqual(res.confidence, 0.0)
        self.assertIsNone(res.skill_name)

    async def test_word_boundary_avoids_substring_false_positive(self):
        registry.register(name="timers", handler=_dummy, keywords=["time"])
        # 'time' is a substring of 'sometimes' but must NOT match (word boundary).
        res = await HighStage().evaluate("sometimes I wonder", _ctx())
        self.assertEqual(res.confidence, 0.0)

    async def test_case_insensitive(self):
        registry.register(name="timers", handler=_dummy, keywords=["timer"])
        res = await HighStage().evaluate("SET A TIMER", _ctx())
        self.assertEqual(res.skill_name, "timers")

    async def test_longest_keyword_breaks_ties(self):
        registry.register(name="generic", handler=_dummy, keywords=["set"])
        registry.register(name="timers", handler=_dummy, keywords=["set a timer"])
        res = await HighStage().evaluate("set a timer please", _ctx())
        self.assertEqual(res.skill_name, "timers")
        self.assertEqual(res.payload.get("matched_keyword"), "set a timer")


# --- MEDIUM stage (T220) ----------------------------------------------------

class TestMediumStage(_RegistryIsolated, IsolatedAsyncioTestCase):
    def _stage(self, llm, band=(0.5, 0.8), threshold=0.65):
        registry.register(name="tasks", handler=_dummy, examples=["what are my tasks today"])
        return MediumStage(threshold=threshold, classifier=IntentClassifier(), llm=llm, band=band)

    async def test_confidence_clamped_to_band_ceiling(self):
        stage = self._stage(_FakeLLM('{"skill": "tasks", "confidence": 0.99}'))
        res = await stage.evaluate("show my tasks", _ctx())
        self.assertEqual(res.skill_name, "tasks")
        self.assertEqual(res.confidence, 0.8)
        self.assertEqual(res.payload["raw_confidence"], 0.99)

    async def test_low_confidence_does_not_clear_threshold(self):
        stage = self._stage(_FakeLLM('{"skill": "tasks", "confidence": 0.55}'))
        res = await stage.evaluate("hmm", _ctx())
        self.assertLess(res.confidence, 0.65)  # falls through to smalltalk

    async def test_extra_prose_around_json_is_tolerated(self):
        stage = self._stage(_FakeLLM('Sure! {"skill": "tasks", "confidence": 0.9} hope that helps'))
        res = await stage.evaluate("tasks please", _ctx())
        self.assertEqual(res.skill_name, "tasks")

    async def test_malformed_output_never_crashes(self):
        stage = self._stage(_FakeLLM("this is not json at all"))
        res = await stage.evaluate("x", _ctx())
        self.assertEqual(res.confidence, 0.0)
        self.assertIsNone(res.skill_name)

    async def test_none_label_is_no_match(self):
        stage = self._stage(_FakeLLM('{"skill": "none", "confidence": 0.9}'))
        res = await stage.evaluate("x", _ctx())
        self.assertEqual(res.confidence, 0.0)

    async def test_unknown_label_is_no_match(self):
        stage = self._stage(_FakeLLM('{"skill": "weather", "confidence": 0.95}'))
        res = await stage.evaluate("x", _ctx())
        self.assertEqual(res.confidence, 0.0)

    async def test_llm_exception_is_skipped(self):
        stage = self._stage(_FakeLLM(RuntimeError("model down")))
        res = await stage.evaluate("x", _ctx())
        self.assertEqual(res.confidence, 0.0)

    async def test_no_llm_skips_stage(self):
        registry.register(name="tasks", handler=_dummy, examples=["task"])
        stage = MediumStage(classifier=IntentClassifier(), llm=None)
        res = await stage.evaluate("x", _ctx(llm=None))
        self.assertEqual(res.confidence, 0.0)

    async def test_falls_back_to_ctx_local_llm(self):
        registry.register(name="tasks", handler=_dummy, examples=["task"])
        stage = MediumStage(threshold=0.65, classifier=IntentClassifier(), llm=None)
        res = await stage.evaluate("x", _ctx(llm=_FakeLLM('{"skill": "tasks", "confidence": 0.9}')))
        self.assertEqual(res.skill_name, "tasks")


# --- Classifier prompt auto-generation (SC-007) -----------------------------

class TestClassifierPrompt(_RegistryIsolated, TestCase):
    def test_prompt_includes_skill_examples(self):
        registry.register(name="tasks", handler=_dummy, examples=["what are my tasks", "add a task"])
        c = IntentClassifier()
        prompt = c.build_prompt()
        self.assertIn("tasks", prompt)
        self.assertIn("what are my tasks", prompt)
        self.assertIn("tasks", c.skill_names)

    def test_prompt_regenerates_when_skill_added(self):
        registry.register(name="tasks", handler=_dummy, examples=["task one"])
        c = IntentClassifier()
        first = c.build_prompt()
        self.assertNotIn("weather forecast", first)

        registry.register(name="weather", handler=_dummy, examples=["weather forecast"])
        second = c.build_prompt()
        self.assertIn("weather forecast", second)
        self.assertIn("weather", c.skill_names)

    def test_examples_capped_per_skill(self):
        registry.register(name="tasks", handler=_dummy, examples=[f"ex{i}" for i in range(10)])
        prompt = IntentClassifier(max_examples_per_skill=3).build_prompt()
        self.assertIn("ex0", prompt)
        self.assertIn("ex2", prompt)
        self.assertNotIn("ex3", prompt)

    def test_skills_without_examples_excluded(self):
        registry.register(name="keyword_only", handler=_dummy, keywords=["kw"])
        c = IntentClassifier()
        c.build_prompt()
        self.assertNotIn("keyword_only", c.skill_names)


# --- Router resolution: order, threshold, tie-break (T221/FR-010/FR-011) -----

class TestWaterfallRouter(_RegistryIsolated, IsolatedAsyncioTestCase):
    async def test_high_beats_smalltalk(self):
        registry.register(name="timers", handler=_dummy, keywords=["timer"])
        registry.register(name="smalltalk", handler=_dummy)
        router = WaterfallRouter(
            [HighStage(threshold=0.95), SmalltalkStage(name="smalltalk", threshold=0.35)], {}
        )
        res = await router.resolve("set a timer", _ctx())
        self.assertEqual(res.stage, "high")
        self.assertEqual(res.skill_name, "timers")

    async def test_falls_to_smalltalk_floor(self):
        registry.register(name="timers", handler=_dummy, keywords=["timer"])
        registry.register(name="smalltalk", handler=_dummy)
        router = WaterfallRouter(
            [HighStage(threshold=0.95), SmalltalkStage(name="smalltalk", threshold=0.35)], {}
        )
        res = await router.resolve("how are you doing", _ctx())
        self.assertEqual(res.skill_name, "smalltalk")

    async def test_stub_stop_never_short_circuits(self):
        # Regression: a 0.0-threshold stop stub used to win every turn silently.
        registry.register(name="smalltalk", handler=_dummy)
        router = WaterfallRouter(
            [StopStage(name="stop", threshold=1.1), SmalltalkStage(name="smalltalk", threshold=0.35)], {}
        )
        res = await router.resolve("anything at all", _ctx())
        self.assertEqual(res.skill_name, "smalltalk")
        self.assertNotEqual(res.stage, "stop")

    async def test_earlier_stage_wins_tie(self):
        # Two stages both clear their thresholds → earlier in order wins (FR-010).
        registry.register(name="timers", handler=_dummy, keywords=["timer"])
        registry.register(name="smalltalk", handler=_dummy)
        first = HighStage(name="high", threshold=0.5)
        second = HighStage(name="high2", threshold=0.5)
        router = WaterfallRouter([first, second], {})
        res = await router.resolve("set a timer", _ctx())
        self.assertEqual(res.stage, "high")


# --- from_config wiring (T221) ----------------------------------------------

class _FakeConfig:
    def __init__(self, stages, **over):
        self.waterfall_stages = stages
        self.waterfall_thresholds = {"high": 0.95, "medium": 0.65, "low": 0.35}
        self.skills_config = {}
        self.agent_skill = "hermes"  # the AGENT fallback role's claimed skill
        self.llm_base_url = ""  # no classifier LLM built (no network)
        self.llm_api_key = None
        self.llm_model = "x"
        self.classifier_model = None
        self.classifier_max_examples = 5
        self.medium_band_low = 0.5
        self.medium_band_high = 0.8
        for k, v in over.items():
            setattr(self, k, v)


class TestFromConfig(_RegistryIsolated, TestCase):
    def setUp(self):
        super().setUp()
        # The AGENT stage is built only when its claimed skill is registered
        # and available — give every test a usable default claimant.
        registry.register(name="hermes", handler=_dummy)

    def test_builds_stages_in_order(self):
        router = WaterfallRouter.from_config(
            _FakeConfig(["stop", "high", "medium", "smalltalk", "agent"])
        )
        self.assertEqual([s.name for s in router.stages],
                         ["stop", "high", "medium", "smalltalk", "agent"])

    def test_enforces_stop_first_agent_last(self):
        router = WaterfallRouter.from_config(
            _FakeConfig(["high", "agent", "stop", "smalltalk"])
        )
        names = [s.name for s in router.stages]
        self.assertEqual(names[0], "stop")
        self.assertEqual(names[-1], "agent")

    def test_agent_stage_dropped_when_skill_unavailable(self):
        # D3: the claimed skill's available() gate fails ⇒ local-only mode; the
        # stage is dropped with a log line, not an error.
        registry.register(name="gated", handler=_dummy, available=lambda: False)
        router = WaterfallRouter.from_config(
            _FakeConfig(["stop", "high", "smalltalk", "agent"], agent_skill="gated")
        )
        self.assertEqual([s.name for s in router.stages], ["stop", "high", "smalltalk"])

    def test_agent_stage_dropped_when_skill_unregistered(self):
        router = WaterfallRouter.from_config(
            _FakeConfig(["stop", "smalltalk", "agent"], agent_skill="nope")
        )
        self.assertNotIn("agent", [s.name for s in router.stages])

    def test_third_party_skill_claims_role(self):
        # Bring-your-own-agent: any registered skill can hold the fallback role.
        registry.register(name="my_agent", handler=_dummy)
        router = WaterfallRouter.from_config(
            _FakeConfig(["stop", "smalltalk", "agent"], agent_skill="my_agent")
        )
        agent = router.stages[-1]
        self.assertEqual(agent.name, "agent")
        self.assertEqual(agent.agent_skill, "my_agent")

    def test_legacy_hermes_stage_name_aliased(self):
        # Pre-harness-as-skill configs list `- hermes`; it must keep working as
        # an agent stage claiming hermes, with a deprecation warning.
        with self.assertLogs("vocascade.waterfall.router", level="WARNING") as logs:
            router = WaterfallRouter.from_config(
                _FakeConfig(["stop", "smalltalk", "hermes"])
            )
        self.assertEqual(router.stages[-1].name, "agent")
        self.assertEqual(router.stages[-1].agent_skill, "hermes")
        self.assertTrue(any("deprecated" in m for m in logs.output))

    def test_thresholds_from_config(self):
        router = WaterfallRouter.from_config(
            _FakeConfig(["high", "medium", "smalltalk", "agent"])
        )
        by = {s.name: s.threshold for s in router.stages}
        self.assertEqual(by["high"], 0.95)
        self.assertEqual(by["medium"], 0.65)
        self.assertEqual(by["smalltalk"], 0.35)
        self.assertEqual(by["agent"], 0.0)

    def test_system_stage_thresholds(self):
        # STOP/CONVERSE are real system stages (US5): they win on a deterministic
        # 1.0 match against a 0.5 bar, and report 0.0 (no short-circuit) otherwise.
        router = WaterfallRouter.from_config(
            _FakeConfig(["stop", "converse", "smalltalk", "agent"])
        )
        by = {s.name: s.threshold for s in router.stages}
        self.assertEqual(by["stop"], 0.5)
        self.assertEqual(by["converse"], 0.5)

    def test_disabled_stage_flag_respected(self):
        router = WaterfallRouter.from_config(
            _FakeConfig(["high", "smalltalk", "agent"],
                        skills_config={"smalltalk": {"enabled": False}})
        )
        by = {s.name: s.enabled for s in router.stages}
        self.assertFalse(by["smalltalk"])


if __name__ == "__main__":
    unittest.main()
