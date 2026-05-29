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
        self.env_patcher = patch.dict("os.environ", {"OPENCLAW_GATEWAY_TOKEN": "test_token"})
        self.env_patcher.start()

        self.stt_patcher = patch("voice_satellite.server.WhisperSTT")
        self.mock_stt_cls = self.stt_patcher.start()
        self.mock_stt = MagicMock()
        self.mock_stt_cls.return_value = self.mock_stt

        self.tts_patcher = patch("voice_satellite.server.GenieTTSClient")
        self.mock_tts_cls = self.tts_patcher.start()
        self.mock_tts = MagicMock()
        self.mock_tts_cls.return_value = self.mock_tts

        self.filler_patcher = patch("voice_satellite.server.FillerEngine")
        self.mock_filler_cls = self.filler_patcher.start()
        self.mock_filler = MagicMock()
        self.mock_filler_cls.return_value = self.mock_filler

        self.gateway_patcher = patch("voice_satellite.server.OpenClawClient")
        self.mock_gateway_cls = self.gateway_patcher.start()
        self.mock_gateway = MagicMock()
        self.mock_gateway_cls.return_value = self.mock_gateway

        self.mock_tts.load_character = AsyncMock()
        self.mock_gateway.connect = AsyncMock()
        self.mock_gateway.close = AsyncMock()
        self.mock_gateway.sessions_abort = AsyncMock()
        
        async def mock_send_transcript(text, **kwargs):
            run_id = await self.mock_gateway.send_message(
                agent_id="main",
                message=text,
                mode="persistent",
                session_key="voice"
            )
            async for token in self.mock_gateway.stream_response(run_id):
                yield token
        self.mock_gateway.send_transcript = mock_send_transcript
        self.mock_filler.get_filler.return_value = None

        self.factory_patcher = patch("voice_satellite.server.get_gateway_client")
        self.mock_factory = self.factory_patcher.start()
        self.mock_factory.return_value = self.mock_gateway

        self.client_ctx = TestClient(app)
        self.client = self.client_ctx.__enter__()

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)
        self.stt_patcher.stop()
        self.tts_patcher.stop()
        self.filler_patcher.stop()
        self.gateway_patcher.stop()
        self.factory_patcher.stop()
        self.env_patcher.stop()

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

            # 4. Check acknowledging and active_listening states
            received_messages = []
            while True:
                msg = ws.receive_json()
                received_messages.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            states = [m.get("state") for m in received_messages if m.get("type") == "status"]
            self.assertIn("acknowledging", states)
            self.assertIn("active_listening", states)

            # 5. Send binary PCM (should not raise error or disconnect)
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

                # Receive acknowledging, active_listening, and possible filler messages
                while True:
                    msg = ws.receive_json()
                    if msg.get("type") == "status" and msg.get("state") == "active_listening":
                        break

                # Verify start_silence_timer was called when entering active_listening
                mock_start.assert_called()

                # Send set_timeout, which resets the timer
                mock_start.reset_mock()
                ws.send_json({"type": "set_timeout", "seconds": 45.0})

                # Brief sleep to allow the async message handling to run
                time.sleep(0.1)

                mock_start.assert_called()

    def test_full_voice_pipeline_direct_openclaw(self):
        """Verifies that transcripts go directly to OpenClaw and the response is spoken."""
        app.state.openclaw_client.send_message = AsyncMock(return_value="run-1")

        async def mock_stream_resp(run_id=None):
            yield "Hello there."
        app.state.openclaw_client.stream_response = MagicMock(side_effect=mock_stream_resp)

        with self.client.websocket_connect("/ws") as ws:

            # Mock STT on app state
            mock_stt = AsyncMock()
            mock_stt.transcribe.return_value = "hello world"
            app.state.stt = mock_stt

            # Mock TTS on app state
            mock_tts = MagicMock()
            mock_tts.degraded_mode = False
            mock_tts.stop = AsyncMock()

            async def mock_synth(text, **kwargs):
                yield b"audio_chunk_1"
            mock_tts.synthesize.side_effect = mock_synth
            app.state.tts = mock_tts

            ws.receive_json()  # passive_listening

            ws.send_json({"type": "wakeword"})
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            ws.send_bytes(b"user_speech_pcm")

            received_messages = []
            while True:
                msg = ws.receive_json()
                received_messages.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

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
            app.state.openclaw_client.send_message.assert_called_once()
            call_kwargs = app.state.openclaw_client.send_message.call_args
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

    def test_barge_in_context_note_injected_for_long_partial(self):
        """
        When a barge-in partial response has ≥10 words, the next outgoing message
        to OpenClaw must be prepended with a context note.
        """
        from voice_satellite.session.state_machine import ConversationSession

        app.state.openclaw_client.send_message = AsyncMock(return_value="run-1")

        async def mock_stream_resp(run_id=None):
            yield "Response word one two three four five six seven eight nine ten eleven twelve."
        app.state.openclaw_client.stream_response = MagicMock(side_effect=mock_stream_resp)

        with self.client.websocket_connect("/ws") as ws:

            mock_stt = AsyncMock()
            mock_stt.transcribe.return_value = "can you say that again"
            app.state.stt = mock_stt

            mock_tts = MagicMock()
            mock_tts.degraded_mode = False
            mock_tts.stop = AsyncMock()

            async def mock_synth(text, **kwargs):
                yield b"chunk"
            mock_tts.synthesize.side_effect = mock_synth
            app.state.tts = mock_tts

            ws.receive_json()  # passive_listening

            ws.send_json({"type": "wakeword"})
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            # Start a generation first
            ws.send_bytes(b"first_pcm")

            # Wait until it is speaking
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "speaking":
                    break

            # Simulate a barge-in that had a long partial response (≥10 words)
            ws.send_json({"type": "playback_progress", "words_played": 11})
            ws.send_json({"type": "interrupt"})

            # drain interrupt response messages
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            # Now send the follow-up audio (transcript: "can you say that again")
            ws.send_bytes(b"follow_up_pcm")

            # Read until the turn is complete
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            # Verify send_message was called and the second call prepended the context note
            self.assertEqual(app.state.openclaw_client.send_message.call_count, 2)
            last_call_args = app.state.openclaw_client.send_message.call_args_list[-1]
            outgoing_text = last_call_args.kwargs.get("message") or last_call_args[1].get("message")
            self.assertIn("[System Note: The last assistant response was interrupted by the user after saying:", outgoing_text)

    def test_tts_degraded_mode_retry(self):
        app.state.openclaw_client.send_message = AsyncMock(return_value="run-1")

        async def mock_stream_resp(run_id=None):
            yield "hello there"
        app.state.openclaw_client.stream_response = MagicMock(side_effect=mock_stream_resp)

        with self.client.websocket_connect("/ws") as ws:

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
            async def mock_synth(text, **kwargs):
                yield b"audio_chunk_1"
            mock_tts.synthesize.side_effect = mock_synth
            app.state.tts = mock_tts

            ws.receive_json()  # passive_listening
            ws.send_json({"type": "wakeword"})
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            ws.send_bytes(b"some_pcm")

            # Read messages until we get active_listening
            received_messages = []
            while True:
                msg = ws.receive_json()
                received_messages.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break
            received_types = [m.get("type") for m in received_messages]

            # Since load_character recovered the client, we expect audio and audio_end to be sent
            self.assertIn("audio", received_types)
            self.assertEqual(len(load_calls), 1)

    def test_gateway_connection_error_graceful_handling(self):
        import httpx

        # Mock send_transcript to raise a connection error
        async def mock_failed_send_transcript(text, **kwargs):
            raise httpx.ConnectError("Connection refused")
            yield ""
        self.mock_gateway.send_transcript = mock_failed_send_transcript

        # Setup mock STT
        mock_stt = AsyncMock()
        mock_stt.transcribe.return_value = "hello"
        app.state.stt = mock_stt

        # Mock TTS on app state
        mock_tts = MagicMock()
        mock_tts.degraded_mode = False
        mock_tts.stop = AsyncMock()
        async def mock_synth(text, **kwargs):
            yield b"error_pcm"
        mock_tts.synthesize.side_effect = mock_synth
        app.state.tts = mock_tts

        with self.client.websocket_connect("/ws") as ws:
            ws.receive_json()  # passive_listening
            ws.send_json({"type": "wakeword"})
            ws.receive_json()  # acknowledging
            ws.receive_json()  # active_listening

            ws.send_bytes(b"some_pcm")

            # Receive messages until we get to status: active_listening
            received_messages = []
            while True:
                msg = ws.receive_json()
                received_messages.append(msg)
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            # Verify that TTS was asked to speak the connection failure message
            assistant_response = next(
                (m for m in received_messages if m.get("type") == "assistant_response"), None
            )
            self.assertIsNotNone(assistant_response)
            self.assertIn("cannot connect to the gateway backend", assistant_response["text"])

            # Verify websocket error message was sent
            error_msg = next(
                (m for m in received_messages if m.get("type") == "error"), None
            )
            self.assertIsNotNone(error_msg)
            self.assertEqual(error_msg["message"], "Connection to gateway backend failed")

    def test_get_gateway_client_factory(self):
        # Temporarily stop the patchers to test actual instantiation logic
        self.factory_patcher.stop()
        self.gateway_patcher.stop()

        try:
            from voice_satellite.server import get_gateway_client
            from voice_satellite.gateway.hermes_client import HermesClient
            from voice_satellite.gateway.openclaw_client import OpenClawClient
            from voice_satellite.config import SatelliteConfig

            # Create a mock config for hermes
            cfg_hermes = MagicMock(spec=SatelliteConfig)
            cfg_hermes.gateway_backend = "hermes"
            cfg_hermes.hermes_base_url = "http://localhost:8642/v1"
            cfg_hermes.hermes_api_key = "test_key"

            client = get_gateway_client(cfg_hermes)
            self.assertIsInstance(client, HermesClient)
            self.assertEqual(client.base_url, "http://localhost:8642/v1")
            self.assertEqual(client.api_key, "test_key")

            # Create a mock config for openclaw
            cfg_openclaw = MagicMock(spec=SatelliteConfig)
            cfg_openclaw.gateway_backend = "openclaw"
            cfg_openclaw.gateway_url = "ws://localhost:8000"
            cfg_openclaw.gateway_token = "token"
            cfg_openclaw.gateway_min_protocol = 1
            cfg_openclaw.gateway_max_protocol = 1

            client_oc = get_gateway_client(cfg_openclaw)
            self.assertIsInstance(client_oc, OpenClawClient)
        finally:
            # Restore patchers for other tests
            self.gateway_patcher.start()
            self.factory_patcher.start()

    @patch("voice_satellite.server.ConversationSession")
    def test_barge_in_history_tracking(self, mock_session_cls):
        from voice_satellite.session.state_machine import ConversationSession
        real_session = ConversationSession()
        mock_session_cls.return_value = real_session

        app.state.openclaw_client.send_message = AsyncMock(return_value="run-1")
        async def mock_stream_resp(run_id=None):
            yield "Response word one two three four five six seven eight nine ten."
        app.state.openclaw_client.stream_response = MagicMock(side_effect=mock_stream_resp)

        with self.client.websocket_connect("/ws") as ws:
            mock_stt = AsyncMock()
            mock_stt.transcribe.return_value = "hello there"
            app.state.stt = mock_stt

            mock_tts = MagicMock()
            mock_tts.degraded_mode = False
            mock_tts.stop = AsyncMock()
            async def mock_synth(text, **kwargs):
                yield b"chunk"
            mock_tts.synthesize.side_effect = mock_synth
            app.state.tts = mock_tts

            ws.receive_json()  # passive_listening

            # Trigger wakeword and send user audio
            ws.send_json({"type": "wakeword"})
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            ws.send_bytes(b"user_audio")
            
            # Wait until it starts speaking
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "speaking":
                    break

            # Now send playback progress and interrupt
            ws.send_json({"type": "playback_progress", "words_played": 4})
            ws.send_json({"type": "interrupt"})

            # Drain messages until back to active listening
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "status" and msg.get("state") == "active_listening":
                    break

            # Verify history contains:
            # 1. user turn: "hello there"
            # 2. assistant turn: partial response with "[Interrupted by user]"
            self.assertEqual(len(real_session.history), 2)
            self.assertEqual(real_session.history[0], {"role": "user", "content": "hello there"})
            self.assertEqual(real_session.history[1], {"role": "assistant", "content": "Response word one two [Interrupted by user]"})


if __name__ == "__main__":
    unittest.main()
