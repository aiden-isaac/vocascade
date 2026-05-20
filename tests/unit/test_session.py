import asyncio
import unittest
from unittest.mock import Mock, AsyncMock
from voice_satellite.session import SessionState, ConversationSession

class TestSession(unittest.IsolatedAsyncioTestCase):
    def test_default_state(self):
        session = ConversationSession()
        self.assertEqual(session.state, SessionState.PASSIVE_LISTENING)
        self.assertTrue(session.is_passive())
        self.assertFalse(session.is_active())
        self.assertFalse(session.is_busy())

    def test_valid_state_transitions(self):
        session = ConversationSession()
        
        # passive_listening -> acknowledging
        session.state = SessionState.ACKNOWLEDGING
        self.assertEqual(session.state, SessionState.ACKNOWLEDGING)
        
        # acknowledging -> active_listening
        session.state = SessionState.ACTIVE_LISTENING
        self.assertEqual(session.state, SessionState.ACTIVE_LISTENING)
        self.assertTrue(session.is_active())
        self.assertFalse(session.is_busy())
        
        # active_listening -> transcribing
        session.state = SessionState.TRANSCRIBING
        self.assertEqual(session.state, SessionState.TRANSCRIBING)
        
        # transcribing -> thinking
        session.state = SessionState.THINKING
        self.assertEqual(session.state, SessionState.THINKING)
        self.assertTrue(session.is_busy())
        
        # thinking -> speaking
        session.state = SessionState.SPEAKING
        self.assertEqual(session.state, SessionState.SPEAKING)
        self.assertTrue(session.is_busy())
        
        # speaking -> interrupted
        session.state = SessionState.INTERRUPTED
        self.assertEqual(session.state, SessionState.INTERRUPTED)
        self.assertFalse(session.is_busy())
        
        # interrupted -> active_listening
        session.state = SessionState.ACTIVE_LISTENING
        self.assertEqual(session.state, SessionState.ACTIVE_LISTENING)

    def test_invalid_state_transition_raises_error(self):
        session = ConversationSession()
        with self.assertRaises(ValueError):
            session.state = SessionState.TRANSCRIBING

    def test_reset_to_passive_always_allowed(self):
        session = ConversationSession()
        session.state = SessionState.ACKNOWLEDGING
        session.state = SessionState.ACTIVE_LISTENING
        session.state = SessionState.TRANSCRIBING
        session.state = SessionState.THINKING
        
        session.state = SessionState.PASSIVE_LISTENING
        self.assertEqual(session.state, SessionState.PASSIVE_LISTENING)

    def test_state_change_callback(self):
        session = ConversationSession()
        callback = Mock()
        session.set_state_change_callback(callback)
        
        session.state = SessionState.ACKNOWLEDGING
        callback.assert_called_once_with(SessionState.PASSIVE_LISTENING, SessionState.ACKNOWLEDGING)

    async def test_silence_timer_expiry(self):
        session = ConversationSession(silence_timeout=0.01)
        callback = AsyncMock()
        session.set_silence_callback(callback)
        
        session.start_silence_timer()
        self.assertIsNotNone(session.silence_timer)
        
        await asyncio.sleep(0.02)
        callback.assert_called_once()
        self.assertIsNone(session.silence_timer)

    async def test_cancel_silence_timer(self):
        session = ConversationSession(silence_timeout=10.0)
        session.start_silence_timer()
        self.assertIsNotNone(session.silence_timer)
        
        session.cancel_silence_timer()
        self.assertIsNone(session.silence_timer)
        await asyncio.sleep(0.01) # Give loop time to clean up if any

if __name__ == "__main__":
    unittest.main()
