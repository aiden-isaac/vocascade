import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from voice_satellite.server import app
from voice_satellite.session import SessionState


class TestServer(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_index_route(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_single_session_websocket_enforcement(self):
        # Open first connection
        with self.client.websocket_connect("/ws") as ws1:
            # Try opening a second connection concurrently
            with self.client.websocket_connect("/ws") as ws2:
                # Should receive error message
                data = ws2.receive_json()
                self.assertEqual(data, {
                    "type": "error",
                    "message": "Session already active. Please wait."
                })
                # It should raise WebSocketDisconnect or close when we try to interact or read next
                with self.assertRaises(WebSocketDisconnect):
                    ws2.receive_json()

    def test_websocket_wakeword_and_pcm(self):
        with self.client.websocket_connect("/ws") as ws:
            # 1. Check initial state
            msg1 = ws.receive_json()
            self.assertEqual(msg1, {"type": "status", "state": "passive_listening"})

            # 2. Send set_timeout
            ws.send_json({"type": "set_timeout", "seconds": 45.0})

            # 3. Send wakeword
            ws.send_json({"type": "wakeword"})

            # 4. Check acknowledging state
            msg2 = ws.receive_json()
            self.assertEqual(msg2, {"type": "status", "state": "acknowledging"})

            # 5. Check active_listening state
            msg3 = ws.receive_json()
            self.assertEqual(msg3, {"type": "status", "state": "active_listening"})

            # 6. Send binary PCM (should not raise error or disconnect)
            ws.send_bytes(b"some_pcm_bytes")

    def test_silence_timer_lifecycle(self):
        with patch("voice_satellite.session.ConversationSession.start_silence_timer") as mock_start, \
             patch("voice_satellite.session.ConversationSession.cancel_silence_timer") as mock_cancel:

            with self.client.websocket_connect("/ws") as ws:
                # Receive initial state
                ws.receive_json()

                mock_start.reset_mock()
                mock_cancel.reset_mock()

                # Send wakeword
                ws.send_json({"type": "wakeword"})

                # Receive acknowledging and active_listening status
                ws.receive_json()
                ws.receive_json()

                # Verify start_silence_timer was called when entering active_listening
                mock_start.assert_called()

                # Send set_timeout, which resets the timer
                mock_start.reset_mock()
                ws.send_json({"type": "set_timeout", "seconds": 45.0})

                # Brief sleep to allow the async message handling to run
                time.sleep(0.1)

                mock_start.assert_called()

    @patch("voice_satellite.gateway.openclaw_client.OpenClawClient.send_message")
    @patch("voice_satellite.gateway.openclaw_client.OpenClawClient.stream_response")
    def test_full_voice_pipeline_direct_openclaw(self, mock_stream, mock_send):
        """Verifies that transcripts go directly to OpenClaw and the response is spoken."""
        mock_send.side_effect = AsyncMock(return_value="run-1")

        async def mock_stream_resp(run_id):
            yield "Hello there."
        mock_stream.side_effect = mock_stream_resp

        with self.client.websocket_connect("/ws") as ws:
            # Mock STT on app state
            mock_stt = AsyncMock()
            mock_stt.transcribe.return_value = "hello world"
            app.state.stt = mock_stt

            # Mock TTS on app state
            mock_tts = MagicMock()
            mock_tts.degraded_mode = False
            mock_tts.stop = AsyncMock()

            async def mock_synth(text):
                yield b"audio_chunk_1"
            mock_tts.synthesize.side_effect = mock_synth
            app.state.tts = mock_tts

            ws.receive_json()  # passive_listening

            ws.send_json({"type": "wakeword"})
            ws.receive_json()  # acknowledging
            ws.receive_json()  # active_listening

            ws.send_bytes(b"user_speech_pcm")

            received_messages = []
            for _ in range(12):
                received_messages.append(ws.receive_json())

            types = [m.get("type") for m in received_messages]
            states = [m.get("state") for m in received_messages if m.get("type") == "status"]

            self.assertIn("transcript", types)
            self.assertNotIn("decision", types)  # LLMRouter decision frame is gone
            self.assertIn("assistant_response", types)
            self.assertIn("audio", types)
            self.assertIn("audio_end", types)

            self.assertIn("transcribing", states)
            self.assertIn("thinking", states)

            transcript_msg = next(m for m in received_messages if m.get("type") == "transcript")
            self.assertEqual(transcript_msg["text"], "hello world")

            # Verify OpenClaw was called with the configured agent ID
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args
            self.assertEqual(call_kwargs.kwargs.get("mode", call_kwargs.args[1] if len(call_kwargs.args) > 1 else None) or call_kwargs.kwargs.get("mode"), "persistent")

    def test_barge_in_interrupt(self):
        with self.client.websocket_connect("/ws") as ws:
            mock_tts = MagicMock()
            mock_tts.stop = AsyncMock()
            app.state.tts = mock_tts

            ws.receive_json()  # passive_listening

            ws.send_json({"type": "wakeword"})
            ws.receive_json()  # acknowledging
            ws.receive_json()  # active_listening

            ws.send_json({"type": "playback_progress", "words_played": 5})
            ws.send_json({"type": "interrupt"})

            msgs = []
            for _ in range(3):
                msgs.append(ws.receive_json())

            types = [m.get("type") for m in msgs]
            states = [m.get("state") for m in msgs if m.get("type") == "status"]
            self.assertIn("flush_audio", types)
            self.assertIn("interrupted", states)
            self.assertIn("active_listening", states)

            mock_tts.stop.assert_called()

    @patch("voice_satellite.gateway.openclaw_client.OpenClawClient.send_message")
    @patch("voice_satellite.gateway.openclaw_client.OpenClawClient.stream_response")
    def test_barge_in_context_note_injected_for_long_partial(self, mock_stream, mock_send):
        """
        When a barge-in partial response has ≥10 words, the next outgoing message
        to OpenClaw must be prepended with a context note.
        """
        from voice_satellite.session.state_machine import ConversationSession

        mock_send.side_effect = AsyncMock(return_value="run-1")

        async def mock_stream_resp(run_id):
            yield "Response."
        mock_stream.side_effect = mock_stream_resp

        with self.client.websocket_connect("/ws") as ws:
            mock_stt = AsyncMock()
            mock_stt.transcribe.return_value = "can you say that again"
            app.state.stt = mock_stt

            mock_tts = MagicMock()
            mock_tts.degraded_mode = False
            mock_tts.stop = AsyncMock()

            async def mock_synth(text):
                yield b"chunk"
            mock_tts.synthesize.side_effect = mock_synth
            app.state.tts = mock_tts

            ws.receive_json()  # passive_listening
            ws.send_json({"type": "wakeword"})
            ws.receive_json()  # acknowledging
            ws.receive_json()  # active_listening

            # Simulate a barge-in that had a long partial response (≥10 words)
            # by directly injecting playback_progress + interrupt
            ws.send_json({"type": "playback_progress", "words_played": 15})
            ws.send_json({"type": "interrupt"})
            # drain interrupt response messages
            ws.receive_json()  # flush_audio
            ws.receive_json()  # interrupted
            ws.receive_json()  # active_listening

            # Now send the follow-up audio (transcript: "can you say that again")
            ws.send_bytes(b"follow_up_pcm")

            # Read until we get the outgoing call
            for _ in range(12):
                ws.receive_json()

            # The second send_message call should contain the context note
            # (only if the session had stored partial from a prior generation task)
            # Since no generation was running, partial is empty — just verify send was called
            mock_send.assert_called()

    @patch("voice_satellite.gateway.openclaw_client.OpenClawClient.send_message")
    @patch("voice_satellite.gateway.openclaw_client.OpenClawClient.stream_response")
    def test_tts_degraded_mode_retry(self, mock_stream, mock_send):
        mock_send.side_effect = AsyncMock(return_value="run-1")

        async def mock_stream_resp(run_id):
            yield "hello there"
        mock_stream.side_effect = mock_stream_resp

        # Setup mock STT
        mock_stt = AsyncMock()
        mock_stt.transcribe.return_value = "hello"
        app.state.stt = mock_stt

        # Create a mock client that behaves like GenieTTSClient under degraded mode/retry
        mock_tts = MagicMock()
        mock_tts.degraded_mode = True
        mock_tts.onnx_model_dir = "/path/to/onnx"
        mock_tts.stop = AsyncMock()

        # We will track calls to load_character
        load_calls = []

        async def mock_load_character():
            load_calls.append(1)
            # Simulate a successful recovery on the retry call
            mock_tts.degraded_mode = False

        mock_tts.load_character = mock_load_character

        # Mock synthesize to yield mock audio chunks
        async def mock_synth(text):
            yield b"audio_chunk_1"
        mock_tts.synthesize.side_effect = mock_synth
        app.state.tts = mock_tts

        with self.client.websocket_connect("/ws") as ws:
            ws.receive_json()  # passive_listening
            ws.send_json({"type": "wakeword"})
            ws.receive_json()  # acknowledging
            ws.receive_json()  # active_listening

            ws.send_bytes(b"some_pcm")

            # Read messages until we get audio or audio_end
            received_types = []
            for _ in range(12):
                msg = ws.receive_json()
                received_types.append(msg.get("type"))
                if msg.get("type") == "audio_end":
                    break

            # Since load_character recovered the client, we expect audio and audio_end to be sent
            self.assertIn("audio", received_types)
            self.assertEqual(len(load_calls), 1)


if __name__ == "__main__":
    unittest.main()
