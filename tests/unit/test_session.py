"""
tests/unit/test_session.py — Session lifecycle + teardown detection (US5 / T233).
"""

import unittest
from unittest import TestCase

from vocascade.session.state import SessionState, SessionStateEnum
from vocascade.session.state_machine import SessionMachine
from vocascade.session.teardown import is_farewell, contains_sentinel, strip_sentinel
from vocascade.waterfall.stages.stop import is_stop


class TestTeardownDetection(TestCase):
    def test_farewell_phrases(self):
        for phrase in ["that will be all", "that'll be all", "goodbye", "I'm done",
                       "see you later", "good night", "nothing else"]:
            self.assertTrue(is_farewell(phrase), phrase)

    def test_non_farewell(self):
        for phrase in ["what's the weather", "set a timer", "tell me a joke",
                       "all of them", "are we done yet"]:
            self.assertFalse(is_farewell(phrase), phrase)

    def test_sentinel_detection(self):
        self.assertTrue(contains_sentinel("Goodbye.\nENDSESSION"))
        self.assertTrue(contains_sentinel("bye end session"))      # spaced/split
        self.assertFalse(contains_sentinel("the session was nice"))

    def test_strip_sentinel(self):
        self.assertEqual(strip_sentinel("Goodbye. ENDSESSION").strip(), "Goodbye.")
        self.assertEqual(strip_sentinel("Bye.\nend session").strip(), "Bye.")

    def test_is_stop(self):
        for phrase in ["stop", "Stop it.", "cancel", "never mind", "be quiet"]:
            self.assertTrue(is_stop(phrase), phrase)
        for phrase in ["stop the timer", "stopwatch", "cancel my meeting"]:
            self.assertFalse(is_stop(phrase), phrase)


class TestSessionMachine(TestCase):
    def _m(self):
        return SessionMachine(SessionState(voice_session_id="s1"))

    def test_wake_activates(self):
        m = self._m()
        m.on_wake()
        self.assertEqual(m.session_state, SessionStateEnum.ACTIVE)
        self.assertEqual(m.state.wake_count, 1)

    def test_speaking_cycle(self):
        m = self._m()
        m.on_wake()
        m.on_bot_started()
        self.assertEqual(m.session_state, SessionStateEnum.SPEAKING)
        m.on_bot_stopped()
        self.assertEqual(m.session_state, SessionStateEnum.ACTIVE)

    def test_teardown_to_passive_retains_flag_reset(self):
        m = self._m()
        m.on_wake()
        m.arm_teardown()
        self.assertTrue(m.should_teardown)
        m.on_teardown()
        self.assertEqual(m.session_state, SessionStateEnum.PASSIVE)
        self.assertFalse(m.should_teardown)

    def test_reengage_disarms_teardown(self):
        m = self._m()
        m.arm_teardown()
        m.on_user_engaged()
        self.assertFalse(m.should_teardown)

    def test_note_reply_sentinel_arms(self):
        m = self._m()
        m.note_reply("Goodbye.\nENDSESSION")
        self.assertTrue(m.should_teardown)

    def test_note_reply_plain_does_not_arm(self):
        m = self._m()
        m.note_reply("Here is the weather forecast.")
        self.assertFalse(m.should_teardown)

    def test_stop_returns_to_active(self):
        m = self._m()
        m.on_wake()
        m.on_bot_started()
        m.on_stop()
        self.assertEqual(m.session_state, SessionStateEnum.ACTIVE)


if __name__ == "__main__":
    unittest.main()
