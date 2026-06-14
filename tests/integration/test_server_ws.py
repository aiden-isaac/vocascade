"""
tests/integration/test_server_ws.py — Server-level WS round-trip.

Drives the *real* FastAPI `/ws` endpoint (the wiring T213 was supposed to add)
end-to-end with mocked STT / local-LLM / Genie, proving an utterance flows
transport → VAD → STT → waterfall router → TTS → transport-out and the client
receives transcript + assistant_response + status messages. This is distinct
from test_pipeline_roundtrip.py, which hand-assembles the stages without the
server.
"""

import copy
import json
import sys
import importlib
import dataclasses
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi.testclient import TestClient

from vocascade.config import load_config
from vocascade.skills.registry import registry


def _config_with_smalltalk_enabled():
    """The committed config.yaml disables smalltalk (Hermes-first); re-enable it
    so the smalltalk round-trip is hermetic and never reaches the live Hermes."""
    base = load_config()
    skills = copy.deepcopy(base.skills_config)
    skills.setdefault("smalltalk", {})["enabled"] = True
    return dataclasses.replace(base, skills_config=skills)


def _force_register_bundled_skills():
    """Register smalltalk/hermes into a fresh registry, bypassing the import cache."""
    registry.clear()
    for mod in ("vocascade.skills.base_skills.smalltalk",
                "vocascade.skills.base_skills.hermes"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
        else:
            importlib.import_module(mod)


async def _empty_synth(text, **kwargs):
    # Async generator that yields no audio (deterministic message set).
    return
    yield  # noqa: unreachable — makes this an async generator


class TestServerWebSocket(unittest.TestCase):
    def setUp(self):
        _force_register_bundled_skills()

    def tearDown(self):
        registry.clear()

    def test_ws_roundtrip_speaks_smalltalk(self):
        # Mock STT instance built in lifespan.
        mock_stt = MagicMock()
        mock_stt.transcribe = AsyncMock(return_value="hello there")
        mock_stt.close = MagicMock()

        # Mock local-LLM used by the smalltalk skill.
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value="Hi, I am here.")

        # Mock Genie client (no network, no audio).
        mock_tts_client = MagicMock()
        mock_tts_client.load_character = AsyncMock()
        mock_tts_client.stop = AsyncMock()
        mock_tts_client.close = AsyncMock()
        mock_tts_client.synthesize = MagicMock(side_effect=_empty_synth)

        with patch("vocascade.adapter.load_config", return_value=_config_with_smalltalk_enabled()), \
             patch("vocascade.adapter.WhisperSTT", return_value=mock_stt), \
             patch("vocascade.gateway.local_llm.LocalLLM", return_value=mock_llm), \
             patch("vocascade.pipeline.tts.GenieTTSClient", return_value=mock_tts_client):
            from vocascade.adapter import app

            with TestClient(app) as client:
                with client.websocket_connect("/ws") as ws:
                    # One complete utterance (client-side VAD already endpointed it).
                    ws.send_bytes(b"\x00" * 3200)

                    msgs = [json.loads(ws.receive_text()) for _ in range(5)]

        types = [m["type"] for m in msgs]
        self.assertIn("transcript", types)
        self.assertIn("assistant_response", types)

        transcript = next(m for m in msgs if m["type"] == "transcript")
        self.assertEqual(transcript["text"], "hello there")

        reply = next(m for m in msgs if m["type"] == "assistant_response")
        self.assertEqual(reply["text"], "Hi, I am here.")

        # STT actually received the utterance bytes.
        mock_stt.transcribe.assert_awaited_once()
        self.assertEqual(mock_stt.transcribe.call_args[0][0], b"\x00" * 3200)

        # Status returns to active_listening so the client can speak again.
        self.assertIn("active_listening", [m.get("state") for m in msgs if m["type"] == "status"])

    def test_wakeword_plays_acknowledge_clip(self):
        mock_stt = MagicMock()
        mock_stt.transcribe = AsyncMock(return_value="")
        mock_stt.close = MagicMock()

        mock_tts_client = MagicMock()
        mock_tts_client.load_character = AsyncMock()
        mock_tts_client.stop = AsyncMock()
        mock_tts_client.close = AsyncMock()
        mock_tts_client.synthesize = MagicMock(side_effect=_empty_synth)

        with patch("vocascade.adapter.WhisperSTT", return_value=mock_stt), \
             patch("vocascade.pipeline.tts.GenieTTSClient", return_value=mock_tts_client):
            from vocascade.adapter import app

            with TestClient(app) as client:
                with client.websocket_connect("/ws") as ws:
                    ws.send_text(json.dumps({"type": "wakeword"}))
                    # active_listening status, then the pre-rendered ack audio.
                    msgs = [json.loads(ws.receive_text()) for _ in range(2)]

        types = [m["type"] for m in msgs]
        self.assertIn("status", types)
        self.assertIn("audio", types)

    def test_second_connection_rejected_while_active(self):
        mock_stt = MagicMock()
        mock_stt.transcribe = AsyncMock(return_value="")
        mock_stt.close = MagicMock()

        with patch("vocascade.adapter.WhisperSTT", return_value=mock_stt):
            from vocascade.adapter import app

            with TestClient(app) as client:
                with client.websocket_connect("/ws"):
                    # A second concurrent session is rejected with an error frame.
                    with client.websocket_connect("/ws") as ws2:
                        err = json.loads(ws2.receive_text())
                        self.assertEqual(err["type"], "error")


if __name__ == "__main__":
    unittest.main()
