import unittest
import os
from unittest.mock import patch
from voice_adapter.config import load_config, AdapterConfig

class TestAdapterConfig(unittest.TestCase):
    def test_default_config_loading(self):
        # Ensure we run in a clean environment for these keys
        env_vars = {
            "HOST": "127.0.0.1",
            "PORT": "9000",
            "AUDIO_IN_SAMPLE_RATE": "16000",
            "AUDIO_OUT_SAMPLE_RATE": "32000",
            "HERMES_BASE_URL": "http://hermes-gateway:8642/v1",
            "HERMES_API_KEY": "secret-key",
            "HERMES_MODEL": "test-qwen",
            "HERMES_SSE_URL": "http://hermes-gateway:8642/v1/tasks/sse",
            "GENIE_TTS_URL": "http://genie-tts:8000",
            "GENIE_CHARACTER_NAME": "test-character",
            "GENIE_ONNX_MODEL_DIR": "/models/genie",
            "GENIE_REFERENCE_AUDIO": "/audio/ref.wav",
            "GENIE_REFERENCE_TEXT": "Reference sentence",
            "GENIE_LANGUAGE": "es",
            "WHISPER_MODEL": "base.en",
            "WHISPER_LANGUAGE": "en",
            "HERMES_MEMORY_PATH": "/path/to/memory",
            "HONCHO_API_URL": "http://honcho:8000",
            "HONCHO_POLL_INTERVAL": "30",
            "LITELLM_HEALTH_URL": "http://litellm/health",
            "OFFLINE_QUEUE_PATH": "/path/to/queue.json",
            "OFFLINE_START_HOUR": "2",
            "OFFLINE_END_HOUR": "6",
            "VOICE_SATELLITE_SKIP_GENIE_INIT": "True"
        }
        
        with patch.dict(os.environ, env_vars):
            config = load_config()
            self.assertEqual(config.host, "127.0.0.1")
            self.assertEqual(config.port, 9000)
            self.assertEqual(config.audio_in_sample_rate, 16000)
            self.assertEqual(config.audio_out_sample_rate, 32000)
            self.assertEqual(config.hermes_base_url, "http://hermes-gateway:8642/v1")
            self.assertEqual(config.hermes_api_key, "secret-key")
            self.assertEqual(config.hermes_model, "test-qwen")
            self.assertEqual(config.hermes_sse_url, "http://hermes-gateway:8642/v1/tasks/sse")
            self.assertEqual(config.tts_url, "http://genie-tts:8000")
            self.assertEqual(config.tts_character_name, "test-character")
            self.assertEqual(config.tts_onnx_model_dir, "/models/genie")
            self.assertEqual(config.tts_reference_audio, "/audio/ref.wav")
            self.assertEqual(config.tts_reference_text, "Reference sentence")
            self.assertEqual(config.tts_language, "es")
            self.assertEqual(config.whisper_model, "base.en")
            self.assertEqual(config.whisper_language, "en")
            self.assertEqual(config.hermes_memory_path, "/path/to/memory")
            self.assertEqual(config.honcho_api_url, "http://honcho:8000")
            self.assertEqual(config.honcho_poll_interval, 30)
            self.assertEqual(config.litellm_health_url, "http://litellm/health")
            self.assertEqual(config.offline_queue_path, "/path/to/queue.json")
            self.assertEqual(config.offline_start_hour, 2)
            self.assertEqual(config.offline_end_hour, 6)
            self.assertTrue(config.skip_genie_init)

    def test_missing_genie_config_warnings(self):
        # Remove genie-specific values to trigger warning
        env_vars = {
            "GENIE_ONNX_MODEL_DIR": "",
            "GENIE_REFERENCE_AUDIO": "",
            "GENIE_REFERENCE_TEXT": ""
        }
        with patch.dict(os.environ, env_vars):
            with patch("voice_adapter.config.logger.warning") as mock_warn:
                config = load_config()
                self.assertIsNone(config.tts_onnx_model_dir)
                self.assertIsNone(config.tts_reference_audio)
                self.assertIsNone(config.tts_reference_text)
                mock_warn.assert_called_once()
                self.assertIn("TTS Configuration incomplete", mock_warn.call_args[0][0])
