import unittest
import os
from unittest.mock import patch
from vocascade.config import load_config, AdapterConfig

# BYOK (D1): LLM_BASE_URL/LLM_MODEL have no defaults — every test that expects
# load_config() to succeed must provide them (and not depend on a local .env).
LLM_ENV = {"LLM_BASE_URL": "http://localhost:11434/v1", "LLM_MODEL": "test-model"}

class TestAdapterConfig(unittest.TestCase):
    def test_default_config_loading(self):
        # Ensure we run in a clean environment for these keys
        env_vars = {
            **LLM_ENV,
            "HOST": "127.0.0.1",
            "PORT": "9000",
            "AUDIO_IN_SAMPLE_RATE": "16000",
            "AUDIO_OUT_SAMPLE_RATE": "32000",
            "HERMES_BASE_URL": "http://hermes-gateway:8642/v1",
            "HERMES_API_KEY": "secret-key",
            "HERMES_MODEL": "test-qwen",
            "HERMES_SESSION_KEY": "test-session-key",
            "HERMES_CONTEXT_SOURCE": "ssh://user@example-host/home/user/.hermes",
            "HERMES_CONTEXT_POLL_INTERVAL": "45",
            "CONTEXT_TOKEN_BUDGET": "900",
            "RESULT_SPEECH_BUDGET": "500",
            "TASK_JOURNAL_PATH": "/path/to/tasks.json",
            "TTS_BACKEND": "genie",
            "GENIE_TTS_URL": "http://genie-tts:8000",
            "GENIE_CHARACTER_NAME": "test-character",
            "GENIE_ONNX_MODEL_DIR": "/models/genie",
            "GENIE_REFERENCE_AUDIO": "/audio/ref.wav",
            "GENIE_REFERENCE_TEXT": "Reference sentence",
            "GENIE_LANGUAGE": "es",
            "WHISPER_MODEL": "base.en",
            "WHISPER_LANGUAGE": "en",
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
            self.assertEqual(config.hermes_session_key, "test-session-key")
            self.assertEqual(config.hermes_context_source, "ssh://user@example-host/home/user/.hermes")
            self.assertEqual(config.hermes_context_poll_interval, 45)
            self.assertEqual(config.context_token_budget, 900)
            self.assertEqual(config.result_speech_budget, 500)
            self.assertEqual(config.task_journal_path, "/path/to/tasks.json")
            self.assertEqual(config.tts_url, "http://genie-tts:8000")
            self.assertEqual(config.tts_character_name, "test-character")
            self.assertEqual(config.tts_onnx_model_dir, "/models/genie")
            self.assertEqual(config.tts_reference_audio, "/audio/ref.wav")
            self.assertEqual(config.tts_reference_text, "Reference sentence")
            self.assertEqual(config.tts_language, "es")
            self.assertEqual(config.whisper_model, "base.en")
            self.assertEqual(config.whisper_language, "en")
            self.assertEqual(config.honcho_api_url, "http://honcho:8000")
            self.assertEqual(config.honcho_poll_interval, 30)
            self.assertEqual(config.litellm_health_url, "http://litellm/health")
            self.assertEqual(config.offline_queue_path, "/path/to/queue.json")
            self.assertEqual(config.offline_start_hour, 2)
            self.assertEqual(config.offline_end_hour, 6)
            self.assertTrue(config.skip_genie_init)
            
            # Assert config.yaml values loaded
            self.assertEqual(config.role, "both")
            self.assertEqual(config.transport_auth_mode, "trust-network")
            self.assertFalse(config.server_vad_enabled)

    def test_005_defaults(self):
        # Clean environment + no .env so the documented defaults apply
        # (LLM values are required — provided, but everything else defaults)
        with patch.dict(os.environ, LLM_ENV, clear=True):
            with patch("vocascade.config.load_dotenv"):
                config = load_config()
                self.assertEqual(config.hermes_session_key, "voice-satellite")
                self.assertEqual(config.hermes_context_source, "none")
                self.assertEqual(config.hermes_context_poll_interval, 30)
                self.assertEqual(config.context_token_budget, 1200)
                self.assertEqual(config.result_speech_budget, 600)
                self.assertEqual(
                    config.task_journal_path,
                    os.path.expanduser("~/.vocascade/tasks.json"),
                )
                # Honcho is strictly opt-in now: empty url = disabled
                self.assertEqual(config.honcho_api_url, "")
                # Removed 004-era fields must be gone entirely
                self.assertFalse(hasattr(config, "hermes_sse_url"))
                self.assertFalse(hasattr(config, "hermes_memory_path"))
                self.assertEqual(config.role, "both")
                self.assertEqual(config.transport_auth_mode, "trust-network")
                self.assertFalse(config.server_vad_enabled)


    def test_missing_genie_config_warnings(self):
        # Remove genie-specific values to trigger warning (genie selected —
        # under the default piper backend these keys are never consulted)
        env_vars = {
            **LLM_ENV,
            "TTS_BACKEND": "genie",
            "GENIE_ONNX_MODEL_DIR": "",
            "GENIE_REFERENCE_AUDIO": "",
            "GENIE_REFERENCE_TEXT": ""
        }
        with patch.dict(os.environ, env_vars):
            with patch("vocascade.config.logger.warning") as mock_warn:
                config = load_config()
                self.assertIsNone(config.tts_onnx_model_dir)
                self.assertIsNone(config.tts_reference_audio)
                self.assertIsNone(config.tts_reference_text)
                mock_warn.assert_called_once()
                self.assertIn("TTS Configuration incomplete", mock_warn.call_args[0][0])

    def test_missing_config_yaml(self):
        with patch.dict(os.environ, {"VOCASCADE_CONFIG_PATH": "non_existent.yaml"}):
            with self.assertRaises(FileNotFoundError):
                load_config()

    def test_malformed_config_yaml(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("system:\n  role: both\n  transport_auth_mode: [unclosed list")
            f_path = f.name
        try:
            with patch.dict(os.environ, {"VOCASCADE_CONFIG_PATH": f_path}):
                with self.assertRaises(ValueError) as ctx:
                    load_config()
                self.assertIn("malformed", str(ctx.exception))
        finally:
            os.unlink(f_path)

    def test_missing_required_keys_config_yaml(self):
        import tempfile
        # Missing system section
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("waterfall:\n  stages: []\n  thresholds: {}\nskills: {}")
            f_path = f.name
        try:
            with patch.dict(os.environ, {"VOCASCADE_CONFIG_PATH": f_path}):
                with self.assertRaises(ValueError) as ctx:
                    load_config()
                self.assertIn("missing required section: 'system'", str(ctx.exception))
        finally:
            os.unlink(f_path)


class TestByokRequirements(unittest.TestCase):
    """D1/D2: LLM connection required (fail fast, located message); Hermes optional."""

    def _load(self, env):
        with patch.dict(os.environ, env, clear=True):
            with patch("vocascade.config.load_dotenv"):
                return load_config()

    def test_missing_llm_base_url_fails_fast_with_located_message(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({"LLM_MODEL": "test-model"})
        msg = str(ctx.exception)
        self.assertIn("LLM_BASE_URL", msg)
        self.assertIn("setup_server", msg)  # points at the setup GUI

    def test_missing_llm_model_fails_fast(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({"LLM_BASE_URL": "http://localhost:11434/v1"})
        self.assertIn("LLM_MODEL", str(ctx.exception))

    def test_empty_llm_api_key_accepted(self):
        config = self._load({**LLM_ENV, "LLM_API_KEY": ""})
        self.assertEqual(config.llm_base_url, LLM_ENV["LLM_BASE_URL"])
        self.assertEqual(config.llm_model, LLM_ENV["LLM_MODEL"])

    def test_empty_hermes_base_url_accepted_local_only(self):
        config = self._load({**LLM_ENV, "HERMES_BASE_URL": ""})
        self.assertEqual(config.hermes_base_url, "")

    def test_no_personal_defaults(self):
        config = self._load(LLM_ENV)
        self.assertEqual(config.hermes_base_url, "")
        self.assertNotIn("frizzt", config.llm_base_url)


class TestTtsBackendSelection(unittest.TestCase):
    """Pluggable TTS: piper is the zero-setup default; unknown names fail fast."""

    def _load(self, env):
        with patch.dict(os.environ, env, clear=True):
            with patch("vocascade.config.load_dotenv"):
                return load_config()

    def test_piper_is_default_with_no_tts_config(self):
        config = self._load(LLM_ENV)
        self.assertEqual(config.tts_backend, "piper")
        self.assertEqual(config.tts_voice, "")
        self.assertEqual(config.tts_models_dir, "")

    def test_genie_keys_ignored_under_piper(self):
        # A leftover genie env must not resurrect the old degraded-mode warning.
        with patch("vocascade.config.logger.warning") as mock_warn:
            config = self._load({**LLM_ENV, "GENIE_ONNX_MODEL_DIR": "/models/genie"})
        self.assertIsNone(config.tts_onnx_model_dir)
        for call in mock_warn.call_args_list:
            self.assertNotIn("TTS Configuration incomplete", call[0][0])

    def test_genie_backend_reads_genie_keys(self):
        config = self._load({
            **LLM_ENV,
            "TTS_BACKEND": "genie",
            "GENIE_ONNX_MODEL_DIR": "/models/genie",
            "GENIE_REFERENCE_AUDIO": "/audio/ref.wav",
            "GENIE_REFERENCE_TEXT": "Reference sentence",
        })
        self.assertEqual(config.tts_backend, "genie")
        self.assertEqual(config.tts_onnx_model_dir, "/models/genie")

    def test_unknown_backend_fails_fast_with_located_message(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({**LLM_ENV, "TTS_BACKEND": "espeak"})
        msg = str(ctx.exception)
        self.assertIn("espeak", msg)
        self.assertIn("piper", msg)
        self.assertIn("genie", msg)
        self.assertIn("setup_server", msg)  # points at the setup GUI

    def test_voice_and_models_dir_from_env(self):
        config = self._load({**LLM_ENV, "TTS_VOICE": "male",
                             "TTS_MODELS_DIR": "/opt/voices"})
        self.assertEqual(config.tts_voice, "male")
        self.assertEqual(config.tts_models_dir, "/opt/voices")

