"""
tests/unit/test_user_skills.py — Bundled/user skills + per-skill config (US6 / T238).

Covers disabled-skill exclusion, user-skill discovery + import isolation, and the
bundled datetime/timers skills (incl. CONVERSE + proactive firing).
"""

import os
import sys
import time
import asyncio
import shutil
import tempfile
import unittest
from unittest import TestCase, IsolatedAsyncioTestCase

from vocascade.skills.registry import registry
from vocascade.skills.context import SkillContext, ToolBag
from vocascade.session.state import SessionState
from vocascade.skills.base_skills.datetime import handle_datetime
from vocascade.skills.base_skills.timers import handle_timers, _parse_duration, _format_duration


async def _h(intent, entities, ctx):
    return "ok"


def _ctx():
    return SkillContext(tools=ToolBag(), session=SessionState(voice_session_id="s1"))


# --- per-skill config (FR-023, acceptance 3) --------------------------------

class TestRegistryConfigure(TestCase):
    def setUp(self):
        registry.clear()

    def tearDown(self):
        registry.clear()

    def test_disabled_skill_unregistered(self):
        registry.register(name="alpha", handler=_h)
        registry.register(name="beta", handler=_h)
        registry.configure({"alpha": {"enabled": False}, "beta": {"enabled": True}})
        self.assertIsNone(registry.get_skill("alpha"))
        self.assertIsNotNone(registry.get_skill("beta"))

    def test_missing_entry_stays_enabled(self):
        registry.register(name="alpha", handler=_h)
        registry.configure({})
        self.assertIsNotNone(registry.get_skill("alpha"))

    def test_config_block_attached(self):
        registry.register(name="alpha", handler=_h)
        registry.configure({"alpha": {"enabled": True, "provider": "todoist", "filler": "thinking"}})
        self.assertEqual(registry.get_skill("alpha").config["provider"], "todoist")


# --- user-skill discovery + isolation (FR-022/024, acceptance 2/4) ----------

class TestUserSkillDiscovery(TestCase):
    def setUp(self):
        registry.clear()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        registry.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.tmp in sys.path:
            sys.path.remove(self.tmp)
        for m in ("user_skills.good_skill", "user_skills.broken_skill"):
            sys.modules.pop(m, None)

    def _write(self, name, body):
        with open(os.path.join(self.tmp, name), "w") as f:
            f.write(body)

    def test_valid_discovered_broken_isolated(self):
        self._write("good_skill.py",
                    "from vocascade.skills import skill\n"
                    "@skill(name='good_user_skill', keywords=['frobnicate'])\n"
                    "async def h(i, e, c):\n    return 'frobnicated'\n")
        self._write("broken_skill.py", "raise RuntimeError('boom on import')\n")

        # Must not raise despite the broken file.
        registry.discover_user_skills(self.tmp)

        good = registry.get_skill("good_user_skill")
        self.assertIsNotNone(good)
        self.assertEqual(good.source, "user")
        self.assertIsNone(registry.get_skill("broken_skill"))

    def test_discovered_then_disabled_by_config(self):
        self._write("cfg_skill.py",
                    "from vocascade.skills import skill\n"
                    "@skill(name='cfg_user_skill')\n"
                    "async def h(i, e, c):\n    return 'x'\n")
        registry.discover_user_skills(self.tmp)
        self.assertIsNotNone(registry.get_skill("cfg_user_skill"))
        registry.configure({"cfg_user_skill": {"enabled": False}})
        self.assertIsNone(registry.get_skill("cfg_user_skill"))


# --- datetime skill (T235) --------------------------------------------------

class TestDatetimeSkill(IsolatedAsyncioTestCase):
    async def test_time_query(self):
        out = await handle_datetime("what time is it", {}, _ctx())
        self.assertTrue(out.startswith("It's"))

    async def test_date_query(self):
        out = await handle_datetime("what's the date today", {}, _ctx())
        self.assertTrue(out.startswith("Today is"))


# --- timers skill (T234) ----------------------------------------------------

class TestTimersSkill(IsolatedAsyncioTestCase):
    def test_parse_duration(self):
        self.assertEqual(_parse_duration("set a timer for 5 minutes"), 300)
        self.assertEqual(_parse_duration("30 second timer"), 30)
        self.assertEqual(_parse_duration("1 hour 30 minutes"), 5400)
        self.assertEqual(_parse_duration("ten minutes"), 600)
        self.assertEqual(_parse_duration("half an hour"), 1800)
        self.assertIsNone(_parse_duration("set a timer"))

    def test_format_duration(self):
        self.assertEqual(_format_duration(300), "5 minutes")
        self.assertEqual(_format_duration(90), "1 minute and 30 seconds")

    async def test_missing_duration_asks_and_claims(self):
        ctx = _ctx()
        out = await handle_timers("set a timer", {}, ctx)
        self.assertEqual(out, "For how long?")
        self.assertIsNotNone(ctx.session.converse_claim)
        self.assertEqual(ctx.session.converse_claim.skill_name, "timers")
        # The claim resumes with the duration.
        resumed = await ctx.session.converse_claim.resume("five minutes", ctx)
        self.assertEqual(resumed, "Timer set for 5 minutes.")

    async def test_timer_fires_via_notify(self):
        fired = []

        async def notify(text):
            fired.append(text)

        ctx = SkillContext(tools=ToolBag(), session=SessionState(), notify=notify)
        # A 0-second timer exercises the fire→notify path without a real wait.
        from vocascade.skills.base_skills.timers import _start_timer
        out = _start_timer(0, ctx)
        self.assertEqual(out, "Timer set for 0 seconds.")
        await asyncio.sleep(0.05)   # let the scheduled fire task run
        self.assertEqual(fired, ["Your 0 seconds timer is up."])

    async def test_timer_without_notify_channel_is_silent(self):
        # No notify wired (e.g. degraded) → still confirms, just won't fire.
        ctx = SkillContext(tools=ToolBag(), session=SessionState(), notify=None)
        out = await handle_timers("set a timer for 5 minutes", {}, ctx)
        self.assertEqual(out, "Timer set for 5 minutes.")


if __name__ == "__main__":
    unittest.main()
