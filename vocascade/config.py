"""
Centralized configuration loader for the vocascade voice stack.
Loads settings from both config.yaml and environment variables/dotenv,
exposing a validated frozen dataclass.
"""

import os
import sys
import logging
from dataclasses import dataclass
from pathlib import Path
import yaml
from dotenv import load_dotenv

logger = logging.getLogger("vocascade.config")

# Ephemeral per-run system prompt sent to Hermes on voice dispatches so the agent
# emits speech-shaped replies (short, no markdown) WITHOUT changing its profile
# config or reasoning. Voice-only: Telegram/CLI runs never carry this.
DEFAULT_HERMES_VOICE_INSTRUCTIONS = (
    "Reply for text-to-speech. Lead with the answer in 1-3 short spoken "
    "sentences. No markdown, lists, headings, code, emoji, or URLs. Say symbols "
    "as words. Keep it brief and conversational."
)

@dataclass(frozen=True)
class AdapterConfig:
    # System role and authentication (from config.yaml)
    role: str                           # "both" | "edge" | "server"
    transport_auth_mode: str            # "trust-network" | "device-identity"
    server_vad_enabled: bool            # True to run Silero VAD on server, False for Edge VAD shim



    # Waterfall routing (from config.yaml)
    waterfall_stages: list[str]
    waterfall_thresholds: dict[str, float]

    # Skills config (from config.yaml)
    skills_config: dict[str, dict]

    # Transport settings (from env/defaults)
    host: str
    port: int
    audio_in_sample_rate: int   # 16000
    audio_out_sample_rate: int  # 32000

    # Local LLM
    llm_base_url: str
    llm_api_key: str | None
    llm_model: str

    # Hermes agent backend
    hermes_base_url: str
    hermes_api_key: str | None
    hermes_model: str
    hermes_session_key: str             # stable X-Hermes-Session-Key memory scope
    hermes_context_source: str          # file://… | ssh://… | none
    hermes_context_poll_interval: int   # seconds, ssh source
    context_token_budget: int           # tokens (~4 chars/token) for prompt block
    result_speech_budget: int           # chars before result condensation
    task_journal_path: str              # persisted task journal

    # Genie TTS (read only when tts_backend == "genie")
    tts_url: str
    tts_character_name: str
    tts_onnx_model_dir: str | None
    tts_reference_audio: str | None
    tts_reference_text: str | None
    tts_language: str

    # STT
    whisper_model: str
    whisper_language: str

    # Pre-rendered acknowledgement / filler audio
    filler_dir: Path

    # Pre-fetch cache enrichment (optional; empty url = disabled)
    honcho_api_url: str
    honcho_poll_interval: int   # 20-30 seconds

    # Offline handler
    litellm_health_url: str     # LiteLLM /health endpoint
    offline_queue_path: str     # ~/.hermes/offline_queue.json
    offline_start_hour: int     # 1
    offline_end_hour: int       # 5

    # Feature flags
    skip_genie_init: bool

    # Medium-stage intent classifier (OQ-5; from config.yaml `waterfall`).
    # Defaulted so callers that build AdapterConfig directly (tests) need not set them.
    classifier_model: str | None = None      # blank/None = reuse llm_model
    classifier_max_examples: int = 5         # examples per skill in the prompt
    medium_band_low: float = 0.5             # classifier confidence clamp floor
    medium_band_high: float = 0.8            # classifier confidence clamp ceiling
    classifier_timeout_seconds: float = 6.0  # short — a hung LLM must not stall routing (US7)

    # Latency masking fillers (US4; from config.yaml `latency`).
    filler_mode: str = "hybrid"              # pool | llm | hybrid
    filler_interval_seconds: float = 3.0     # gap before the first follow-up filler
    filler_backoff: bool = True              # widen the gap after each follow-up
    filler_max: int = 3                      # max follow-up lines (0 off, -1 unlimited)

    # Device-identity transport auth (OQ-3/US8; paths from env per OQ-4).
    # Edge signs the server nonce with identity_key_path; server checks the
    # presented public key against authorized_keys_path (None = no allowlist).
    identity_key_path: str = "~/.vocascade/identity.pem"
    authorized_keys_path: str | None = None

    # Session-end memory gist write endpoint (US10; empty url = disabled).
    memory_summary_url: str = ""

    # Ephemeral TTS-shaping system prompt sent with every voice run (does not
    # touch the Hermes profile config / reasoning; voice-only).
    hermes_voice_instructions: str = DEFAULT_HERMES_VOICE_INSTRUCTIONS

    # Sensitivity/tuning knobs (issue #171), all env-driven like the other
    # device/model settings. Edge wake-word threshold lives in edge/_load_edge_config.
    vad_threshold: float = 0.5          # server Silero VAD speech probability cutoff
    vad_min_silence_ms: int = 250       # silence held before speech is declared ended
    vad_speech_pad_ms: int = 50         # padding kept around each detected utterance
    whisper_beam_size: int = 1          # Whisper beam search width (1 = fastest)
    whisper_vad_filter: bool = False    # let Whisper drop non-speech before transcribing
    tts_volume: float = 1.0             # software gain on TTS PCM (1.0 = unchanged)

    # Pluggable TTS backend (vocascade/tts/protocol.py). Piper is the
    # zero-setup default; "genie" selects the custom voice-cloning path.
    tts_backend: str = "piper"
    tts_voice: str = ""                 # friendly alias (female/male) or raw piper voice id; "" = backend default
    tts_models_dir: str = ""            # piper voice files; "" = ~/.local/share/vocascade/piper


def _parse_bool(val: str | None) -> bool:
    if not val:
        return False
    val = val.strip().lower()
    return val in ("true", "1", "yes", "on")


def load_config() -> AdapterConfig:
    """
    Loads configuration from config.yaml and environment variables (including .env file).
    Fails fast if config.yaml is missing, malformed, or missing required structure.
    Warns and enables degraded TTS mode if voice-cloning configurations are missing.
    """
    # Load dotenv if available
    load_dotenv()

    # Determine config file path
    config_path = os.getenv("VOCASCADE_CONFIG_PATH", "config.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"Configuration file '{config_path}' not found. "
            "Please ensure config.yaml is present in the repository root or set VOCASCADE_CONFIG_PATH."
        )

    # Load and parse config.yaml
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Configuration file '{config_path}' is malformed: {e}")
    except Exception as e:
        raise ValueError(f"Error reading configuration file '{config_path}': {e}")

    if not yaml_config:
        raise ValueError(f"Configuration file '{config_path}' is empty.")

    # Validate structure
    for section in ("system", "waterfall", "skills"):
        if section not in yaml_config:
            raise ValueError(f"Configuration file '{config_path}' is missing required section: '{section}'")

    system = yaml_config["system"]
    if not isinstance(system, dict) or "role" not in system or "transport_auth_mode" not in system or "server_vad_enabled" not in system:
        raise ValueError(f"Configuration file '{config_path}': 'system' section must contain 'role', 'transport_auth_mode', and 'server_vad_enabled'")


    waterfall = yaml_config["waterfall"]
    if not isinstance(waterfall, dict) or "stages" not in waterfall or "thresholds" not in waterfall:
        raise ValueError(f"Configuration file '{config_path}': 'waterfall' section must contain 'stages' and 'thresholds'")

    # Medium-stage classifier config (OQ-5) — all optional with defaults.
    classifier_model = waterfall.get("classifier_model") or None
    classifier_max_examples = int(waterfall.get("max_examples_per_skill", 5))
    classifier_timeout_seconds = float(waterfall.get("classifier_timeout_seconds", 6.0))
    band = waterfall.get("medium_band", [0.5, 0.8])
    try:
        medium_band_low, medium_band_high = float(band[0]), float(band[1])
    except (TypeError, IndexError, ValueError):
        logger.warning("Invalid waterfall.medium_band %r; using default [0.5, 0.8]", band)
        medium_band_low, medium_band_high = 0.5, 0.8

    skills = yaml_config["skills"]
    if not isinstance(skills, dict):
        raise ValueError(f"Configuration file '{config_path}': 'skills' section must be a dictionary")

    # Latency masking (US4) — optional section, all keys defaulted.
    latency_cfg = yaml_config.get("latency") or {}
    filler_mode = str(latency_cfg.get("filler_mode", "hybrid"))
    filler_interval_seconds = float(latency_cfg.get("filler_interval_seconds", 3.0))
    filler_backoff = bool(latency_cfg.get("filler_backoff", True))
    filler_max = int(latency_cfg.get("filler_max", 3))

    # TTS backend selection: piper (zero-setup default) or genie (voice cloning).
    # Validated against the registry so a typo fails startup with the fix.
    tts_backend = (os.getenv("TTS_BACKEND") or "piper").strip().lower()
    from vocascade.tts.protocol import REGISTRY  # function-level: avoids config↔protocol import cycle
    if tts_backend not in REGISTRY:
        raise ValueError(
            f"TTS_BACKEND '{tts_backend}' is not a registered TTS backend "
            f"(registered: {', '.join(sorted(REGISTRY))}). Fix it in .env or run "
            "the setup GUI: python -m vocascade.setup_server"
        )

    # Genie-only voice-cloning keys — consulted (and warned about) only when
    # the genie backend is selected; piper installs need none of them.
    tts_onnx_model_dir = tts_reference_audio = tts_reference_text = None
    if tts_backend == "genie":
        tts_onnx_model_dir = os.getenv("GENIE_ONNX_MODEL_DIR") or None
        tts_reference_audio = os.getenv("GENIE_REFERENCE_AUDIO") or None
        tts_reference_text = os.getenv("GENIE_REFERENCE_TEXT") or None

        if not tts_onnx_model_dir:
            logger.warning(
                "TTS Configuration incomplete. Missing: GENIE_ONNX_MODEL_DIR. "
                "Genie TTS will run in degraded mode (text responses only, no voice cloning)."
            )

    # BYOK fast brain (D1): required, no baked-in default — fail fast with the fix.
    llm_base_url = (os.getenv("LLM_BASE_URL") or "").strip()
    llm_model = (os.getenv("LLM_MODEL") or "").strip()
    for key, val in (("LLM_BASE_URL", llm_base_url), ("LLM_MODEL", llm_model)):
        if not val:
            raise ValueError(
                f"{key} is not set. Point it at any OpenAI-compatible endpoint "
                "(Ollama, llama.cpp-server, OpenRouter, ...) in .env, or run the "
                "setup GUI: python -m vocascade.setup_server"
            )

    # Hermes heavy brain (D2): optional — empty/unset means local-only mode
    # (no hermes stage, no task broker).
    hermes_base_url = (os.getenv("HERMES_BASE_URL") or "").strip()

    # Resolve default paths
    default_offline_queue = os.path.expanduser("~/.hermes/offline_queue.json")
    default_journal_path = os.path.expanduser("~/.vocascade/tasks.json")

    return AdapterConfig(
        role=system["role"],
        transport_auth_mode=system["transport_auth_mode"],
        server_vad_enabled=system["server_vad_enabled"],
        waterfall_stages=waterfall["stages"],

        waterfall_thresholds=waterfall["thresholds"],
        skills_config=skills,

        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        audio_in_sample_rate=int(os.getenv("AUDIO_IN_SAMPLE_RATE", "16000")),
        audio_out_sample_rate=int(os.getenv("AUDIO_OUT_SAMPLE_RATE", "32000")),

        llm_base_url=llm_base_url,
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_model=llm_model,

        hermes_base_url=hermes_base_url,
        hermes_api_key=os.getenv("HERMES_API_KEY"),
        hermes_model=os.getenv("HERMES_MODEL", "hermes-agent"),
        hermes_session_key=os.getenv("HERMES_SESSION_KEY", "voice-satellite"),
        hermes_context_source=os.getenv("HERMES_CONTEXT_SOURCE", "none"),
        hermes_context_poll_interval=int(os.getenv("HERMES_CONTEXT_POLL_INTERVAL", "30")),
        hermes_voice_instructions=os.getenv(
            "HERMES_VOICE_INSTRUCTIONS", DEFAULT_HERMES_VOICE_INSTRUCTIONS),
        context_token_budget=int(os.getenv("CONTEXT_TOKEN_BUDGET", "1200")),
        result_speech_budget=int(os.getenv("RESULT_SPEECH_BUDGET", "600")),
        task_journal_path=os.getenv("TASK_JOURNAL_PATH", default_journal_path),

        tts_url=os.getenv("GENIE_TTS_URL", "http://127.0.0.1:8000"),
        tts_character_name=os.getenv("GENIE_CHARACTER_NAME", "default"),
        tts_onnx_model_dir=tts_onnx_model_dir,
        tts_reference_audio=tts_reference_audio,
        tts_reference_text=tts_reference_text,
        tts_language=os.getenv("GENIE_LANGUAGE", "en"),
        tts_backend=tts_backend,
        tts_voice=(os.getenv("TTS_VOICE") or "").strip(),
        tts_models_dir=(os.getenv("TTS_MODELS_DIR") or "").strip(),

        whisper_model=os.getenv("WHISPER_MODEL", "tiny.en"),
        whisper_language=os.getenv("WHISPER_LANGUAGE", "en"),
        whisper_beam_size=int(os.getenv("WHISPER_BEAM_SIZE", "1")),
        whisper_vad_filter=_parse_bool(os.getenv("WHISPER_VAD_FILTER", "false")),

        vad_threshold=float(os.getenv("VAD_THRESHOLD", "0.5")),
        vad_min_silence_ms=int(os.getenv("VAD_MIN_SILENCE_MS", "250")),
        vad_speech_pad_ms=int(os.getenv("VAD_SPEECH_PAD_MS", "50")),
        tts_volume=float(os.getenv("TTS_VOLUME", "1.0")),

        filler_dir=Path(os.getenv("FILLER_DIR", "static/fillers")),

        honcho_api_url=os.getenv("HONCHO_API_URL", ""),
        honcho_poll_interval=int(os.getenv("HONCHO_POLL_INTERVAL", "25")),

        litellm_health_url=os.getenv("LITELLM_HEALTH_URL", "http://localhost:4000/health"),
        offline_queue_path=os.getenv("OFFLINE_QUEUE_PATH", default_offline_queue),
        offline_start_hour=int(os.getenv("OFFLINE_START_HOUR", "1")),
        offline_end_hour=int(os.getenv("OFFLINE_END_HOUR", "5")),

        skip_genie_init=_parse_bool(os.getenv("VOICE_SATELLITE_SKIP_GENIE_INIT", "False")),

        classifier_model=classifier_model,
        classifier_max_examples=classifier_max_examples,
        medium_band_low=medium_band_low,
        medium_band_high=medium_band_high,
        classifier_timeout_seconds=classifier_timeout_seconds,

        filler_mode=filler_mode,
        filler_interval_seconds=filler_interval_seconds,
        filler_backoff=filler_backoff,
        filler_max=filler_max,

        identity_key_path=os.path.expanduser(
            os.getenv("EDGE_IDENTITY_KEY_PATH", "~/.vocascade/identity.pem")),
        authorized_keys_path=(
            os.path.expanduser(os.getenv("AUTHORIZED_KEYS_PATH"))
            if os.getenv("AUTHORIZED_KEYS_PATH") else None),

        memory_summary_url=os.getenv("MEMORY_SUMMARY_URL", ""),
    )
