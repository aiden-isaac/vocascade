import io
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from voice_satellite.__main__ import bootstrap
from voice_satellite.config import SatelliteConfig

class TestMainBootstrap(unittest.IsolatedAsyncioTestCase):
    @patch("voice_satellite.__main__.load_config")
    @patch("voice_satellite.__main__.WhisperSTT")
    @patch("voice_satellite.__main__.GenieTTSClient")
    @patch("voice_satellite.__main__.FillerEngine")
    @patch("voice_satellite.__main__.OpenClawClient")
    @patch("pathlib.Path.exists")
    async def test_bootstrap_health_report(
        self,
        mock_exists,
        mock_openclaw_cls,
        mock_filler_cls,
        mock_genie_cls,
        mock_whisper_cls,
        mock_load_config
    ):
        # Setup config mock
        config = SatelliteConfig(
            litellm_api_key="key",
            litellm_url="url",
            llm_model="model",
            llm_history_messages=10,
            gateway_url="ws://127.0.0.1:18789",
            gateway_token="token",
            gateway_min_protocol=3,
            gateway_max_protocol=4,
            tts_url="http://127.0.0.1:8000",
            tts_character_name="ordis",
            tts_onnx_model_dir="/path/onnx",
            tts_reference_audio="/path/ref.wav",
            tts_reference_text="hello",
            tts_language="en",
            whisper_model="tiny.en",
            whisper_language="en",
            filler_dir=Path("static/fillers"),
            filler_threshold_secs=2.0,
            host="0.0.0.0",
            port=8000,
            skip_genie_init=False
        )
        mock_load_config.return_value = config
        mock_exists.side_effect = [True, False]

        # Setup mock STT
        mock_stt = MagicMock()
        mock_whisper_cls.return_value = mock_stt

        # Setup mock TTS client
        mock_tts = AsyncMock()
        mock_tts.degraded_mode = False
        mock_genie_cls.return_value = mock_tts

        # Setup mock filler engine
        mock_filler = MagicMock()
        mock_filler.load_fillers.return_value = 14
        mock_filler.get_categories.return_value = {
            "thinking": 3,
            "working": 3,
            "acknowledge": 3,
            "slow_task": 3,
            "signoff": 2
        }
        mock_filler_cls.return_value = mock_filler

        # Setup mock openclaw client
        mock_openclaw = AsyncMock()
        mock_openclaw.test_connectivity.return_value = True
        mock_openclaw.protocol = 3
        mock_openclaw_cls.return_value = mock_openclaw

        # Capture print output
        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            res_config = await bootstrap()
        finally:
            sys.stdout = sys.__stdout__

        self.assertEqual(res_config, config)

        report_output = captured_output.getvalue()
        
        # Verify content of the health report box
        self.assertIn("Voice Satellite — Startup Health Report", report_output)
        self.assertIn("Config:      .env loaded ✓", report_output)
        self.assertIn("STT:         tiny.en (CPU) ✓", report_output)
        self.assertIn("TTS:         ordis @ http://127.0.0.1:8000 ✓", report_output)
        self.assertIn("Gateway:     ws://127.0.0.1:18789 (v3) ✓", report_output)
        self.assertIn("Fillers:     14 loaded (thinking: 3, working: 3, acknowledge: 3, slow_task: 3, signoff: 2) ✓", report_output)
        self.assertIn("Wakeword:    None (always active)", report_output)
        self.assertIn("Listening:   http://0.0.0.0:8000", report_output)

if __name__ == "__main__":
    unittest.main()
