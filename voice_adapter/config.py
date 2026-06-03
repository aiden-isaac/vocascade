"""
Centralized configuration loader for the Pipecat Voice Adapter.
Loads settings from environment variables/dotenv and exposes a validated frozen dataclass.
"""

import os
import sys
import logging
from dataclasses import dataclass
from dotenv import load_dotenv

logger = logging.getLogger("voice_adapter.config")

@dataclass(frozen=True)
class AdapterConfig:
    # Pipecat transport
    host: str
    port: int
    audio_in_sample_rate: int   # 16000
    audio_out_sample_rate: int  # 32000

    # Local LLM
    llm_base_url: str
    llm_api_key: str | None
    llm_model: str

    # Hermes gateway
    hermes_base_url: str
    hermes_api_key: str | None
    hermes_model: str
    hermes_sse_url: str         # SSE endpoint for task completions

    # Genie TTS
    tts_url: str
    tts_character_name: str
    tts_onnx_model_dir: str | None
    tts_reference_audio: str | None
    tts_reference_text: str | None
    tts_language: str

    # STT
    whisper_model: str
    whisper_language: str

    # Pre-fetch cache
    hermes_memory_path: str     # ~/.hermes/memory
    honcho_api_url: str         # Honcho on artemis
    honcho_poll_interval: int   # 20-30 seconds

    # Offline handler
    litellm_health_url: str     # LiteLLM /health endpoint
    offline_queue_path: str     # ~/.hermes/offline_queue.json
    offline_start_hour: int     # 1
    offline_end_hour: int       # 5

    # Feature flags
    skip_genie_init: bool


def _parse_bool(val: str | None) -> bool:
    if not val:
        return False
    val = val.strip().lower()
    return val in ("true", "1", "yes", "on")


def load_config() -> AdapterConfig:
    """
    Loads configuration from environment variables (including .env file).
    Warns and enables degraded TTS mode if voice-cloning configurations are missing.
    """
    # Load dotenv if available
    load_dotenv()

    # Optional TTS keys — warn and degrade
    tts_onnx_model_dir = os.getenv("GENIE_ONNX_MODEL_DIR")
    tts_reference_audio = os.getenv("GENIE_REFERENCE_AUDIO")
    tts_reference_text = os.getenv("GENIE_REFERENCE_TEXT")

    tts_missing = []
    if not tts_onnx_model_dir:
        tts_missing.append("GENIE_ONNX_MODEL_DIR")
        tts_onnx_model_dir = None
    if not tts_reference_audio:
        tts_reference_audio = None
    if not tts_reference_text:
        tts_reference_text = None

    if tts_missing:
        logger.warning(
            f"TTS Configuration incomplete. Missing: {', '.join(tts_missing)}. "
            "Genie TTS will run in degraded mode (text responses only, no voice cloning)."
        )

    # Resolve default paths
    default_memory_path = os.path.expanduser("~/.hermes/memory")
    default_offline_queue = os.path.expanduser("~/.hermes/offline_queue.json")

    return AdapterConfig(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        audio_in_sample_rate=int(os.getenv("AUDIO_IN_SAMPLE_RATE", "16000")),
        audio_out_sample_rate=int(os.getenv("AUDIO_OUT_SAMPLE_RATE", "32000")),

        llm_base_url=os.getenv("LLM_BASE_URL", "https://llm.frizzt.com/v1"),
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", "qwen-moe-coder-fast"),

        hermes_base_url=os.getenv("HERMES_BASE_URL", "http://localhost:8642/v1"),
        hermes_api_key=os.getenv("HERMES_API_KEY"),
        hermes_model=os.getenv("HERMES_MODEL", "qwen2.5-coder:7b-instruct-q8_0"),
        hermes_sse_url=os.getenv("HERMES_SSE_URL", "http://localhost:8642/v1/tasks/sse"),

        tts_url=os.getenv("GENIE_TTS_URL", "http://127.0.0.1:8000"),
        tts_character_name=os.getenv("GENIE_CHARACTER_NAME", "default"),
        tts_onnx_model_dir=tts_onnx_model_dir,
        tts_reference_audio=tts_reference_audio,
        tts_reference_text=tts_reference_text,
        tts_language=os.getenv("GENIE_LANGUAGE", "en"),

        whisper_model=os.getenv("WHISPER_MODEL", "tiny.en"),
        whisper_language=os.getenv("WHISPER_LANGUAGE", "en"),

        hermes_memory_path=os.getenv("HERMES_MEMORY_PATH", default_memory_path),
        honcho_api_url=os.getenv("HONCHO_API_URL", "http://artemis:8000"),
        honcho_poll_interval=int(os.getenv("HONCHO_POLL_INTERVAL", "25")),

        litellm_health_url=os.getenv("LITELLM_HEALTH_URL", "http://localhost:4000/health"),
        offline_queue_path=os.getenv("OFFLINE_QUEUE_PATH", default_offline_queue),
        offline_start_hour=int(os.getenv("OFFLINE_START_HOUR", "1")),
        offline_end_hour=int(os.getenv("OFFLINE_END_HOUR", "5")),

        skip_genie_init=_parse_bool(os.getenv("VOICE_SATELLITE_SKIP_GENIE_INIT", "False")),
    )
