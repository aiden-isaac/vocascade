"""
tests/integration/test_pipeline_roundtrip.py — End-to-end integration test of custom pipeline.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from vocascade.pipeline.pipeline import (
    VoicePipeline,
    PipelineStage,
    AudioFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    TranscriptionFrame,
    TextFrame
)
from vocascade.pipeline.vad import VADStage
from vocascade.pipeline.stt import STTStage
from vocascade.pipeline.router import RouterStage
from vocascade.pipeline.tts import GenieTTSStage
from vocascade.waterfall.router import WaterfallRouter
from vocascade.skills.registry import registry
from vocascade.session.state import SessionState
from vocascade.config import AdapterConfig

class MockSpeakerStage(PipelineStage):
    """Pipeline stage acting as the speaker output sink, collecting received frames."""
    def __init__(self):
        super().__init__()
        self.received_frames = []

    async def push(self, frame):
        self.received_frames.append(frame)
        await super().push(frame)


class TestPipelineRoundtrip(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        registry.clear()
        # Force registration of base skills by reloading the modules to bypass
        # the sys.modules import cache (the @skill decorators only run on import).
        import sys
        import importlib
        for mod in ("vocascade.skills.base_skills.smalltalk",
                    "vocascade.skills.base_skills.hermes"):
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
            else:
                importlib.import_module(mod)

    def tearDown(self):
        registry.clear()

    @patch("vocascade.pipeline.tts.GenieTTSClient")
    @patch("vocascade.stt.whisper.WhisperSTT")
    @patch("vocascade.gateway.local_llm.LocalLLM")
    async def test_smalltalk_roundtrip(self, mock_llm_cls, mock_stt_cls, mock_tts_client_cls):
        # 1. Setup Mock STT
        mock_stt = MagicMock()
        mock_stt.transcribe = AsyncMock(return_value="what is the meaning of life")
        mock_stt_cls.return_value = mock_stt

        # 2. Setup Mock Local LLM
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value="Ordis is happy to help.")
        mock_llm_cls.return_value = mock_llm

        # 3. Setup Mock Genie TTS Client
        mock_tts_client = MagicMock()
        mock_tts_client.load_character = AsyncMock()
        mock_tts_client.stop = AsyncMock()
        mock_tts_client.close = AsyncMock()
        async def mock_synthesize(text):
            yield b"\x01\x02"
            yield b"\x03\x04"
        mock_tts_client.synthesize = MagicMock(side_effect=mock_synthesize)
        mock_tts_client_cls.return_value = mock_tts_client

        # 4. Create dummy config
        dummy_config = AdapterConfig(
            role="both",
            transport_auth_mode="trust-network",
            server_vad_enabled=False,
            waterfall_stages=["smalltalk", "hermes"],
            waterfall_thresholds={"low": 0.35},
            skills_config={
                "smalltalk": {"enabled": True, "filler": "thinking"},
                "hermes": {"enabled": True}
            },
            host="127.0.0.1",
            port=8000,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=32000,
            llm_base_url="http://localhost:11434/v1",
            llm_api_key="mock",
            llm_model="qwen",
            hermes_base_url="http://localhost:8642/v1",
            hermes_api_key="mock",
            hermes_model="hermes",
            hermes_session_key="voice-session",
            hermes_context_source="none",
            hermes_context_poll_interval=30,
            context_token_budget=1200,
            result_speech_budget=600,
            task_journal_path="tasks.json",
            tts_url="http://localhost:8000",
            tts_character_name="ordis",
            tts_onnx_model_dir=None,
            tts_reference_audio=None,
            tts_reference_text=None,
            tts_language="en",
            whisper_model="tiny.en",
            whisper_language="en",
            filler_dir=None,
            honcho_api_url="",
            honcho_poll_interval=25,
            litellm_health_url="http://localhost:4000/health",
            offline_queue_path="queue.json",
            offline_start_hour=1,
            offline_end_hour=5,
            skip_genie_init=False
        )

        # 5. Initialize stages
        session_state = SessionState()
        
        router = WaterfallRouter.from_config(dummy_config)
        
        vad_stage = VADStage(server_vad_enabled=False)
        stt_stage = STTStage(whisper_stt=mock_stt)
        router_stage = RouterStage(router=router, session_state=session_state, config=dummy_config)
        tts_stage = GenieTTSStage(
            tts_url=dummy_config.tts_url,
            character_name=dummy_config.tts_character_name,
            degraded_mode=True
        )
        speaker_stage = MockSpeakerStage()

        # Build pipeline
        pipeline = VoicePipeline([
            vad_stage,
            stt_stage,
            router_stage,
            tts_stage,
            speaker_stage
        ])

        # Start pipeline
        await pipeline.start()

        # 6. Push a user audio frame (simulated Edge VAD behavior)
        dummy_audio = b"\x00" * 3200
        await pipeline.push(AudioFrame(audio=dummy_audio))

        # Yield control to allow tasks to complete
        await asyncio.sleep(0.1)

        # Stop pipeline
        await pipeline.stop()
        await tts_stage.close()

        # 7. Assertions
        # Verify STT was called with the accumulated audio bytes
        mock_stt.transcribe.assert_called_once_with(dummy_audio, session="")

        # Verify Local LLM chat was called with expected prompt context
        mock_llm.chat.assert_called_once()
        messages_arg = mock_llm.chat.call_args[0][0]
        self.assertEqual(messages_arg[0]["role"], "system")
        # Should adopt Ordis persona because character name is "ordis"
        self.assertIn("ROLEPLAY INSTRUCTIONS: ORDIS PERSONALITY", messages_arg[0]["content"])
        self.assertEqual(messages_arg[1]["role"], "user")
        self.assertEqual(messages_arg[1]["content"], "what is the meaning of life")

        # Verify speaker stage received Bot speaking frames & synthesized audio
        received_types = [type(f) for f in speaker_stage.received_frames]
        self.assertIn(BotStartedSpeakingFrame, received_types)
        self.assertIn(AudioFrame, received_types)
        self.assertIn(BotStoppedSpeakingFrame, received_types)

        # Verify synthesized audio was received (may be modified by character effects)
        synthesized_audio = b"".join(f.audio for f in speaker_stage.received_frames if isinstance(f, AudioFrame))
        self.assertTrue(len(synthesized_audio) > 0)


    @patch("vocascade.pipeline.tts.GenieTTSClient")
    @patch("vocascade.stt.whisper.WhisperSTT")
    async def test_hermes_fallback_roundtrip(self, mock_stt_cls, mock_tts_client_cls):
        # 1. Setup Mock STT
        mock_stt = MagicMock()
        mock_stt.transcribe = AsyncMock(return_value="tell me a joke")
        mock_stt_cls.return_value = mock_stt

        # 2. Setup Mock Genie TTS Client
        mock_tts_client = MagicMock()
        mock_tts_client.load_character = AsyncMock()
        mock_tts_client.stop = AsyncMock()
        mock_tts_client.close = AsyncMock()
        async def mock_synthesize(text):
            yield b"\x01\x02"
            yield b"\x03\x04"
        mock_tts_client.synthesize = MagicMock(side_effect=mock_synthesize)
        mock_tts_client_cls.return_value = mock_tts_client

        # 3. Create config with smalltalk stage disabled so it falls through to Hermes
        dummy_config = AdapterConfig(
            role="both",
            transport_auth_mode="trust-network",
            server_vad_enabled=False,
            waterfall_stages=["smalltalk", "hermes"],
            waterfall_thresholds={"low": 0.35},
            skills_config={
                "smalltalk": {"enabled": False, "filler": "thinking"},
                "hermes": {"enabled": True}
            },
            host="127.0.0.1",
            port=8000,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=32000,
            llm_base_url="",
            llm_api_key=None,
            llm_model="qwen",
            hermes_base_url="http://localhost:8642/v1",
            hermes_api_key="mock",
            hermes_model="hermes",
            hermes_session_key="voice-session",
            hermes_context_source="none",
            hermes_context_poll_interval=30,
            context_token_budget=1200,
            result_speech_budget=600,
            task_journal_path="tasks.json",
            tts_url="http://localhost:8000",
            tts_character_name="default",
            tts_onnx_model_dir=None,
            tts_reference_audio=None,
            tts_reference_text=None,
            tts_language="en",
            whisper_model="tiny.en",
            whisper_language="en",
            filler_dir=None,
            honcho_api_url="",
            honcho_poll_interval=25,
            litellm_health_url="http://localhost:4000/health",
            offline_queue_path="queue.json",
            offline_start_hour=1,
            offline_end_hour=5,
            skip_genie_init=False
        )

        session_state = SessionState()
        router = WaterfallRouter.from_config(dummy_config)
        
        vad_stage = VADStage(server_vad_enabled=False)
        stt_stage = STTStage(whisper_stt=mock_stt)
        router_stage = RouterStage(router=router, session_state=session_state, config=dummy_config)
        tts_stage = GenieTTSStage(
            tts_url=dummy_config.tts_url,
            character_name=dummy_config.tts_character_name,
            degraded_mode=True
        )
        speaker_stage = MockSpeakerStage()

        # Build pipeline
        pipeline = VoicePipeline([
            vad_stage,
            stt_stage,
            router_stage,
            tts_stage,
            speaker_stage
        ])

        # Start pipeline
        await pipeline.start()

        # Push a user audio frame
        await pipeline.push(AudioFrame(audio=b"\x00" * 1600))
        await asyncio.sleep(0.1)

        await pipeline.stop()
        await tts_stage.close()

        # The hermes skill has no TaskBroker wired in this pipeline (US3 backend
        # is app-level), so it degrades to a graceful spoken notice rather than
        # the old mockup echo.
        self.assertTrue(mock_tts_client.synthesize.called)
        synthesize_text = mock_tts_client.synthesize.call_args[0][0]
        self.assertEqual(synthesize_text, "I can't reach the agent right now.")

        received_types = [type(f) for f in speaker_stage.received_frames]
        self.assertIn(BotStartedSpeakingFrame, received_types)
        self.assertIn(AudioFrame, received_types)
        self.assertIn(BotStoppedSpeakingFrame, received_types)

        synthesized_audio = b"".join(f.audio for f in speaker_stage.received_frames if isinstance(f, AudioFrame))
        self.assertTrue(len(synthesized_audio) > 0)
