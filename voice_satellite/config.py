"""
Centralized configuration loader for the voice satellite client.
Loads settings from environment variables/dotenv and exposes a validated frozen dataclass.
"""

import os
import sys
import logging
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("voice_satellite.config")

@dataclass(frozen=True)
class SatelliteConfig:
    # OpenClaw Gateway settings
    gateway_url: str
    gateway_token: str
    gateway_agent_id: str
    gateway_min_protocol: int
    gateway_max_protocol: int

    # Genie TTS settings
    tts_url: str
    tts_character_name: str
    tts_onnx_model_dir: str | None
    tts_reference_audio: str | None
    tts_reference_text: str | None
    tts_language: str

    # Whisper STT settings
    whisper_model: str
    whisper_language: str

    # Audio / Filler settings
    filler_dir: Path
    filler_threshold_secs: float

    # Server settings
    host: str
    port: int

    # Feature flags
    skip_genie_init: bool


def _parse_bool(val: str | None) -> bool:
    if not val:
        return False
    val = val.strip().lower()
    return val in ("true", "1", "yes", "on")


def load_config() -> SatelliteConfig:
    """
    Loads configuration from environment variables (including .env file).
    Fails fast if OPENCLAW_GATEWAY_TOKEN is missing.
    Warns and enables degraded TTS mode if voice-cloning configurations are missing.
    """
    # Load dotenv if available
    load_dotenv()

    # Required keys — fail-fast
    gateway_token = os.getenv("OPENCLAW_GATEWAY_TOKEN")

    missing = []
    if not gateway_token:
        missing.append("OPENCLAW_GATEWAY_TOKEN")

    if missing:
        print(f"FATAL ERROR: Missing required configuration variables: {', '.join(missing)}", file=sys.stderr)
        print("Please configure them in your .env file.", file=sys.stderr)
        sys.exit(1)

    # Optional TTS keys — warn and degrade
    tts_onnx_model_dir = os.getenv("GENIE_ONNX_MODEL_DIR")
    tts_reference_audio = os.getenv("GENIE_REFERENCE_AUDIO")
    tts_reference_text = os.getenv("GENIE_REFERENCE_TEXT")

    tts_missing = []
    if not tts_onnx_model_dir:
        tts_missing.append("GENIE_ONNX_MODEL_DIR")
    if not tts_reference_audio:
        tts_missing.append("GENIE_REFERENCE_AUDIO")
    if not tts_reference_text:
        tts_missing.append("GENIE_REFERENCE_TEXT")

    if tts_missing:
        logger.warning(
            f"TTS Configuration incomplete. Missing: {', '.join(tts_missing)}. "
            "Genie TTS will run in degraded mode (text responses only, no voice cloning)."
        )

    return SatelliteConfig(
        gateway_url=os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789"),
        gateway_token=gateway_token,
        gateway_agent_id=os.getenv("OPENCLAW_AGENT_ID", "main"),
        gateway_min_protocol=int(os.getenv("GATEWAY_MIN_PROTOCOL", "3")),
        gateway_max_protocol=int(os.getenv("GATEWAY_MAX_PROTOCOL", "4")),

        tts_url=os.getenv("GENIE_TTS_URL", "http://127.0.0.1:8000"),
        tts_character_name=os.getenv("GENIE_CHARACTER_NAME", "ordis"),
        tts_onnx_model_dir=tts_onnx_model_dir,
        tts_reference_audio=tts_reference_audio,
        tts_reference_text=tts_reference_text,
        tts_language=os.getenv("GENIE_LANGUAGE", "en"),

        whisper_model=os.getenv("WHISPER_MODEL", "tiny.en"),
        whisper_language=os.getenv("WHISPER_LANGUAGE", "en"),

        filler_dir=Path(os.getenv("FILLER_DIR", "static/fillers")),
        filler_threshold_secs=float(os.getenv("FILLER_THRESHOLD_SECS", "2.0")),

        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),

        skip_genie_init=_parse_bool(os.getenv("VOICE_SATELLITE_SKIP_GENIE_INIT", "False")),
    )
