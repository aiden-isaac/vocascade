import os
import unittest
from unittest.mock import patch
from pathlib import Path
from voice_satellite.config import load_config, SatelliteConfig

class TestConfig(unittest.TestCase):
    def setUp(self):
        # Patch load_dotenv to prevent loading from .env on disk during unit testing
        self.dotenv_patcher = patch("voice_satellite.config.load_dotenv")
        self.mock_load_dotenv = self.dotenv_patcher.start()

        # Clear environment variables of interest to prevent cross-contamination
        self.env_keys = [
            "LITELLM_API_KEY", "LITELLM_URL", "LLM_MODEL", "LLM_HISTORY_MESSAGES",
            "OPENCLAW_GATEWAY_URL", "OPENCLAW_GATEWAY_TOKEN",
            "GATEWAY_MIN_PROTOCOL", "GATEWAY_MAX_PROTOCOL",
            "GENIE_TTS_URL", "GENIE_CHARACTER_NAME", "GENIE_ONNX_MODEL_DIR",
            "GENIE_REFERENCE_AUDIO", "GENIE_REFERENCE_TEXT", "GENIE_LANGUAGE",
            "WHISPER_MODEL", "WHISPER_LANGUAGE", "FILLER_DIR", "FILLER_THRESHOLD_SECS",
            "HOST", "PORT", "VOICE_SATELLITE_SKIP_GENIE_INIT"
        ]
        self.original_env = {}
        for key in self.env_keys:
            if key in os.environ:
                self.original_env[key] = os.environ[key]
                del os.environ[key]

    def tearDown(self):
        # Stop load_dotenv patcher
        self.dotenv_patcher.stop()

        # Restore environment variables
        for key in self.env_keys:
            if key in os.environ:
                del os.environ[key]
        for key, val in self.original_env.items():
            os.environ[key] = val

    def test_load_config_missing_required_keys_fails(self):
        # Missing LITELLM_API_KEY and OPENCLAW_GATEWAY_TOKEN
        with self.assertRaises(SystemExit) as cm:
            load_config()
        self.assertEqual(cm.exception.code, 1)

        # Missing only OPENCLAW_GATEWAY_TOKEN
        os.environ["LITELLM_API_KEY"] = "test_key"
        with self.assertRaises(SystemExit) as cm:
            load_config()
        self.assertEqual(cm.exception.code, 1)

    def test_load_config_success_with_defaults(self):
        os.environ["LITELLM_API_KEY"] = "test_api_key"
        os.environ["OPENCLAW_GATEWAY_TOKEN"] = "test_gateway_token"

        config = load_config()

        # Check required fields
        self.assertEqual(config.litellm_api_key, "test_api_key")
        self.assertEqual(config.gateway_token, "test_gateway_token")

        # Check default values
        self.assertEqual(config.litellm_url, "https://llm.frizzt.com/v1")
        self.assertEqual(config.llm_model, "qwen-moe-coder-fast")
        self.assertEqual(config.llm_history_messages, 20)
        self.assertEqual(config.gateway_url, "http://127.0.0.1:18789")
        self.assertEqual(config.gateway_min_protocol, 3)
        self.assertEqual(config.gateway_max_protocol, 4)
        self.assertEqual(config.tts_url, "http://127.0.0.1:8000")
        self.assertEqual(config.tts_character_name, "ordis")
        self.assertIsNone(config.tts_onnx_model_dir)
        self.assertIsNone(config.tts_reference_audio)
        self.assertIsNone(config.tts_reference_text)
        self.assertEqual(config.tts_language, "en")
        self.assertEqual(config.whisper_model, "tiny.en")
        self.assertEqual(config.whisper_language, "en")
        self.assertEqual(config.filler_dir, Path("static/fillers"))
        self.assertEqual(config.filler_threshold_secs, 2.0)
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 8000)
        self.assertFalse(config.skip_genie_init)

    def test_load_config_custom_values(self):
        os.environ["LITELLM_API_KEY"] = "custom_api_key"
        os.environ["OPENCLAW_GATEWAY_TOKEN"] = "custom_gateway_token"
        os.environ["LITELLM_URL"] = "http://custom-llm/v1"
        os.environ["LLM_MODEL"] = "custom-model"
        os.environ["LLM_HISTORY_MESSAGES"] = "10"
        os.environ["OPENCLAW_GATEWAY_URL"] = "ws://custom-gateway"
        os.environ["GATEWAY_MIN_PROTOCOL"] = "1"
        os.environ["GATEWAY_MAX_PROTOCOL"] = "2"
        os.environ["GENIE_TTS_URL"] = "http://custom-tts"
        os.environ["GENIE_CHARACTER_NAME"] = "custom_char"
        os.environ["GENIE_ONNX_MODEL_DIR"] = "/path/onnx"
        os.environ["GENIE_REFERENCE_AUDIO"] = "/path/ref.wav"
        os.environ["GENIE_REFERENCE_TEXT"] = "Hello world"
        os.environ["GENIE_LANGUAGE"] = "fr"
        os.environ["WHISPER_MODEL"] = "base"
        os.environ["WHISPER_LANGUAGE"] = "fr"
        os.environ["FILLER_DIR"] = "/path/fillers"
        os.environ["FILLER_THRESHOLD_SECS"] = "1.5"
        os.environ["HOST"] = "127.0.0.1"
        os.environ["PORT"] = "9000"
        os.environ["VOICE_SATELLITE_SKIP_GENIE_INIT"] = "True"

        config = load_config()

        self.assertEqual(config.litellm_api_key, "custom_api_key")
        self.assertEqual(config.gateway_token, "custom_gateway_token")
        self.assertEqual(config.litellm_url, "http://custom-llm/v1")
        self.assertEqual(config.llm_model, "custom-model")
        self.assertEqual(config.llm_history_messages, 10)
        self.assertEqual(config.gateway_url, "ws://custom-gateway")
        self.assertEqual(config.gateway_min_protocol, 1)
        self.assertEqual(config.gateway_max_protocol, 2)
        self.assertEqual(config.tts_url, "http://custom-tts")
        self.assertEqual(config.tts_character_name, "custom_char")
        self.assertEqual(config.tts_onnx_model_dir, "/path/onnx")
        self.assertEqual(config.tts_reference_audio, "/path/ref.wav")
        self.assertEqual(config.tts_reference_text, "Hello world")
        self.assertEqual(config.tts_language, "fr")
        self.assertEqual(config.whisper_model, "base")
        self.assertEqual(config.whisper_language, "fr")
        self.assertEqual(config.filler_dir, Path("/path/fillers"))
        self.assertEqual(config.filler_threshold_secs, 1.5)
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 9000)
        self.assertTrue(config.skip_genie_init)

if __name__ == "__main__":
    unittest.main()
