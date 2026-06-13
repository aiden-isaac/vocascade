import unittest
import time
import asyncio
from vocascade.waterfall.types import ConfidenceResult, WaterfallStage
from vocascade.session.state import SessionState, SessionStateEnum, ConverseClaim

class DummyWaterfallStage(WaterfallStage):
    async def evaluate(self, utterance, ctx):
        return ConfidenceResult(stage=self.name, confidence=1.0)

class TestWaterfallAndSessionState(unittest.IsolatedAsyncioTestCase):
    def test_confidence_result(self):
        res = ConfidenceResult(stage="high", confidence=0.8)
        self.assertEqual(res.stage, "high")
        self.assertEqual(res.confidence, 0.8)
        self.assertEqual(res.skill_name, None)
        self.assertEqual(res.payload, {})

    async def test_waterfall_stage_abc(self):
        stage = DummyWaterfallStage(name="dummy", threshold=0.6, enabled=True)
        self.assertEqual(stage.name, "dummy")
        self.assertEqual(stage.threshold, 0.6)
        self.assertTrue(stage.enabled)
        
        result = await stage.evaluate("test utterance", None)
        self.assertEqual(result.stage, "dummy")
        self.assertEqual(result.confidence, 1.0)

    def test_session_state_defaults(self):
        state = SessionState()
        self.assertEqual(state.state, SessionStateEnum.PASSIVE)
        self.assertIsNone(state.converse_claim)
        self.assertIsInstance(state.interrupt, asyncio.Event)
        self.assertEqual(state.voice_session_id, "")
        self.assertEqual(state.wake_count, 0)
        self.assertLessEqual(state.last_activity_at, time.time())

    def test_session_state_reset_activity(self):
        state = SessionState()
        old_time = state.last_activity_at
        time.sleep(0.01)
        state.reset_activity()
        self.assertGreater(state.last_activity_at, old_time)

if __name__ == "__main__":
    unittest.main()
