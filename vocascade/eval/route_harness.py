"""
vocascade/eval/route_harness.py — Text-in → routing-decision-out harness (US9).

Resolves the full confidence waterfall for an utterance with **no audio, STT, or
TTS** (FR-120), reporting the winning stage, the winning skill and its
confidence, and the per-stage confidence trace (FR-121). Use it to confirm
routing correctness headlessly — including that data/action requests reach the
Hermes stage rather than being swallowed by smalltalk.

CLI::

    PYTHONPATH=. .venv/bin/python -m vocascade.eval.route_harness "what time is it"
    # winning_stage=high  skill=datetime  confidence=1.0
    # trace: stop:0.00  converse:0.00  high:1.00 (WON)

Stages that need the local LLM (medium classifier, smalltalk gate) only engage
when the loaded config points at a reachable LLM; with `LLM_BASE_URL` unset the
harness routes purely on the deterministic stages, which is what the CI fixtures
exercise.
"""

import sys
import asyncio
import logging
from dataclasses import dataclass, field

from vocascade.waterfall.router import WaterfallRouter
from vocascade.skills.registry import registry
from vocascade.skills.context import SkillContext, ToolBag
from vocascade.session.state import SessionState

logger = logging.getLogger("vocascade.eval.route_harness")


@dataclass
class RoutingDecision:
    utterance: str
    winning_stage: str
    skill: str | None
    confidence: float
    trace: list = field(default_factory=list)

    def format_trace(self) -> str:
        parts = []
        for rec in self.trace:
            if rec.get("skipped"):
                parts.append(f"{rec['stage']}:--")
                continue
            if "error" in rec:
                parts.append(f"{rec['stage']}:ERR")
                continue
            tag = " (WON)" if rec.get("won") else ""
            parts.append(f"{rec['stage']}:{rec['confidence']:.2f}{tag}")
        return "  ".join(parts)

    def __str__(self) -> str:
        return (f"winning_stage={self.winning_stage}  skill={self.skill}  "
                f"confidence={self.confidence}\ntrace: {self.format_trace()}")


class RouteHarness:
    """Builds the waterfall once from config and routes utterances through it."""

    def __init__(self, config, llm=None, discover: bool = True):
        self.config = config
        self._llm = llm
        if discover:
            self._ensure_skills()
        self.router = WaterfallRouter.from_config(config)

    def _ensure_skills(self) -> None:
        """Populate the registry exactly as the server's lifespan does, so HIGH
        keywords, the classifier prompt, and the smalltalk floor all exist."""
        registry.discover_bundled_skills()
        try:
            registry.discover_user_skills()
        except Exception as e:  # user skills are best-effort (FR-022)
            logger.warning("User-skill discovery failed in harness: %s", e)
        registry.configure(self.config.skills_config)

    def _build_ctx(self) -> SkillContext:
        config_dict = dict(self.config.skills_config) if self.config.skills_config else {}
        config_dict["tts_character_name"] = getattr(self.config, "tts_character_name", "default")
        llm = self._llm
        if llm is None and getattr(self.config, "llm_base_url", None):
            from vocascade.gateway.local_llm import LocalLLM
            llm = LocalLLM(
                base_url=self.config.llm_base_url,
                api_key=self.config.llm_api_key,
                model=self.config.llm_model,
                timeout=getattr(self.config, "classifier_timeout_seconds", 6.0),
            )
        return SkillContext(
            tools=ToolBag(),
            session=SessionState(voice_session_id="eval"),
            history=[],
            config=config_dict,
            local_llm=llm,
        )

    async def route(self, utterance: str) -> RoutingDecision:
        ctx = self._build_ctx()
        trace: list = []
        result = await self.router.resolve(utterance, ctx, trace=trace)
        return RoutingDecision(
            utterance=utterance,
            winning_stage=result.stage,
            skill=result.skill_name,
            confidence=result.confidence,
            trace=trace,
        )


def _pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


async def _timed_runs(harness: "RouteHarness", utterance: str, n: int):
    """D8 (measure-only): repeat the classification path N times, collecting
    total and per-stage wall time from the trace. Changes nothing."""
    import time
    totals: list[float] = []
    per_stage: dict[str, list[float]] = {}
    decision = None
    for _ in range(n):
        t0 = time.perf_counter()
        decision = await harness.route(utterance)
        totals.append((time.perf_counter() - t0) * 1000.0)
        for rec in decision.trace:
            if "ms" in rec:
                per_stage.setdefault(rec["stage"], []).append(rec["ms"])
    return totals, per_stage, decision


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print('usage: python -m vocascade.eval.route_harness [--time N] "<utterance>"',
              file=sys.stderr)
        return 2

    from vocascade.config import load_config

    if argv[0] == "--time":
        n, utterance = int(argv[1]), " ".join(argv[2:])
        harness = RouteHarness(load_config())
        totals, per_stage, decision = asyncio.run(_timed_runs(harness, utterance, n))
        print(f"utterance={utterance!r}  runs={n}  winner={decision.winning_stage}"
              f"  skill={decision.skill}")
        print(f"{'total':<10} p50={_pct(totals, .5):7.1f}ms  p95={_pct(totals, .95):7.1f}ms")
        for stage, vals in per_stage.items():
            print(f"{stage:<10} p50={_pct(vals, .5):7.1f}ms  p95={_pct(vals, .95):7.1f}ms")
        return 0

    harness = RouteHarness(load_config())
    decision = asyncio.run(harness.route(" ".join(argv)))
    print(decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
