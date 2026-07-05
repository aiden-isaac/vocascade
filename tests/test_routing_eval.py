"""
tests/test_routing_eval.py — Routing eval harness fixtures + CI runner (US9 / T245).

Three layers:
  • Deterministic fixtures (no LLM) — STOP/HIGH/smalltalk-floor routing must hit
    its expected stage with ≥95% accuracy (SC-004/SC-009). Runs in CI.
  • Smalltalk gate (fake LLM) — proves the FR-033 fix wiring deterministically:
    an agent-class utterance makes smalltalk abstain so the Hermes stage wins,
    while genuine chit-chat keeps the smalltalk floor. Runs in CI.
  • Live routing — the `requires_llm` fixtures against the real local LLM; this
    self-skips unless `LLM_BASE_URL` is reachable.
"""

import os
import sys
import json
import socket
import asyncio
import importlib
import pkgutil
import unittest
from pathlib import Path
from urllib.parse import urlparse

from vocascade.eval.route_harness import RouteHarness
from vocascade.skills.registry import registry

FIXTURES = Path(__file__).resolve().parent.parent / "vocascade" / "eval" / "fixtures.jsonl"


def _load_fixtures():
    rows = []
    for line in FIXTURES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _register_bundled_skills():
    """Re-run the @skill decorators even if a prior test cleared the global
    registry while the modules were already imported. Register each exactly once:
    reload if cached, import otherwise (doing both would double-register)."""
    import vocascade.skills.base_skills as bs
    registry.clear()
    for _, mod_name, _ in pkgutil.iter_modules(bs.__path__, bs.__name__ + "."):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            importlib.import_module(mod_name)


class _DetConfig:
    """No-LLM config → medium skipped, smalltalk gate inert: fully deterministic."""
    waterfall_stages = ["stop", "converse", "high", "medium", "smalltalk", "hermes"]
    waterfall_thresholds = {"high": 0.95, "medium": 0.65, "low": 0.35}
    skills_config = {
        "datetime": {"enabled": True},
        "timers": {"enabled": True},
        "smalltalk": {"enabled": True, "gate": True},
        "hermes": {"enabled": True},
        "stop": {"enabled": True},
    }
    tts_character_name = "default"
    hermes_base_url = "http://hermes.test/v1"  # configured → hermes stage kept (D2)
    llm_base_url = ""
    llm_api_key = None
    llm_model = "x"
    classifier_model = None
    classifier_max_examples = 5
    classifier_timeout_seconds = 6.0
    medium_band_low = 0.5
    medium_band_high = 0.8


class TestRoutingEvalDeterministic(unittest.TestCase):
    def setUp(self):
        _register_bundled_skills()
        self.harness = RouteHarness(_DetConfig(), discover=False)

    def test_deterministic_fixtures_meet_accuracy_bar(self):
        rows = [r for r in _load_fixtures() if not r.get("requires_llm")]
        self.assertGreaterEqual(len(rows), 50, "need ≥50 deterministic fixtures (T245)")

        misses = []
        for r in rows:
            decision = asyncio.run(self.harness.route(r["utterance"]))
            if decision.winning_stage != r["expected_stage"]:
                misses.append((r["utterance"], r["expected_stage"], decision.winning_stage))

        accuracy = 1.0 - len(misses) / len(rows)
        self.assertGreaterEqual(
            accuracy, 0.95,
            f"routing accuracy {accuracy:.1%} below 95%; misses: {misses}")

    def test_trace_is_reported(self):
        decision = asyncio.run(self.harness.route("what time is it"))
        self.assertEqual(decision.winning_stage, "high")
        self.assertEqual(decision.skill, "datetime")
        # The trace records every evaluated stage up to the winner.
        stages_seen = [rec["stage"] for rec in decision.trace]
        self.assertIn("stop", stages_seen)
        self.assertIn("high", stages_seen)
        self.assertTrue(decision.format_trace())


class _FakeGateLLM:
    """Stands in for the local LLM: AGENT when the utterance looks agent-class."""
    AGENT_WORDS = ("task", "todoist", "calendar", "email", "weather", "server",
                   "inbox", "news", "stock", "message", "shopping")

    async def chat(self, messages, **kwargs):
        utt = messages[-1]["content"].lower()
        return "AGENT" if any(w in utt for w in self.AGENT_WORDS) else "SMALLTALK"


class TestSmalltalkGate(unittest.TestCase):
    """FR-033: the gate must let agent-class utterances fall through to Hermes."""

    def setUp(self):
        _register_bundled_skills()
        self.harness = RouteHarness(_DetConfig(), llm=_FakeGateLLM(), discover=False)

    def test_agent_query_abstains_to_hermes(self):
        for utt in ["what are my tasks today", "check my todoist tasks",
                    "what's the weather like", "check the hermes server"]:
            decision = asyncio.run(self.harness.route(utt))
            self.assertEqual(decision.winning_stage, "hermes",
                             f"'{utt}' should fall through to hermes, got {decision.winning_stage}")

    def test_chitchat_keeps_the_smalltalk_floor(self):
        for utt in ["how are you", "tell me a joke", "who are you"]:
            decision = asyncio.run(self.harness.route(utt))
            self.assertEqual(decision.winning_stage, "smalltalk",
                             f"'{utt}' should stay smalltalk, got {decision.winning_stage}")


def _llm_reachable() -> bool:
    base = os.getenv("LLM_BASE_URL")
    if not base:
        return False
    u = urlparse(base)
    host = u.hostname
    port = u.port or (443 if u.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _live_eval_enabled() -> bool:
    # Opt-in only: a live LLM is non-deterministic, so this must never run (and
    # never flake) as part of the normal suite. Run it deliberately with
    # `VOCASCADE_LIVE_EVAL=1` against a reachable LLM.
    return os.getenv("VOCASCADE_LIVE_EVAL") == "1" and _llm_reachable()


@unittest.skipUnless(_live_eval_enabled(), "set VOCASCADE_LIVE_EVAL=1 with a reachable LLM")
class TestRoutingEvalLive(unittest.TestCase):
    """The requires_llm fixtures against the real local LLM gate (opt-in spot check)."""

    def setUp(self):
        from vocascade.config import load_config
        _register_bundled_skills()
        self.harness = RouteHarness(load_config(), discover=False)

    def test_agent_fixtures_route_to_hermes(self):
        rows = [r for r in _load_fixtures() if r.get("requires_llm")]
        misses = []
        for r in rows:
            decision = asyncio.run(self.harness.route(r["utterance"]))
            if decision.winning_stage != r["expected_stage"]:
                misses.append((r["utterance"], decision.winning_stage))
        accuracy = 1.0 - len(misses) / len(rows)
        # Tolerant bound — the gate is an LLM call, so allow the occasional miss.
        self.assertGreaterEqual(
            accuracy, 0.83,
            f"live agent-routing accuracy {accuracy:.1%} too low; misses: {misses}")


if __name__ == "__main__":
    unittest.main()
