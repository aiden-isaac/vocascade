"""
vocascade/tts/protocol.py — the TTS backend seam.

``TTSBackend`` captures exactly the surface the pipeline TTS stage consumes;
any backend registered in ``REGISTRY`` must conform. Backends are selected by
name from config — a plain dict in source, no plugin discovery. Factories
import their backend module lazily so selecting one backend never imports the
other's third-party dependencies.
"""

from typing import AsyncIterator, Callable, Protocol, runtime_checkable

from vocascade.config import AdapterConfig


@runtime_checkable
class TTSBackend(Protocol):
    """The surface the TTS pipeline stage consumes.

    Attributes:
        sample_rate: native rate (Hz) of the mono s16le PCM ``synthesize`` yields.
        degraded_mode: True once the backend has given up (text-only mode).
    """

    sample_rate: int
    degraded_mode: bool

    async def start(self) -> None:
        """Load the configured voice; set ``degraded_mode`` on failure (never raise)."""
        ...

    def synthesize(self, text: str, *, session: str = "") -> AsyncIterator[bytes]:
        """Stream mono s16le PCM chunks (even-length) for ``text``."""
        ...

    async def stop(self) -> None:
        """Abort any in-flight synthesis (barge-in)."""
        ...

    async def close(self) -> None:
        """Release backend resources (network sessions, handles)."""
        ...


def _make_genie(config: AdapterConfig, degraded_mode: bool = False) -> "TTSBackend":
    """Build the Genie/GPT-SoVITS voice-cloning client from config."""
    from vocascade.tts.genie_client import GenieTTSClient

    return GenieTTSClient(
        tts_url=config.tts_url,
        character_name=config.tts_character_name,
        onnx_model_dir=config.tts_onnx_model_dir,
        reference_audio=config.tts_reference_audio,
        reference_text=config.tts_reference_text,
        language=config.tts_language,
        degraded_mode=degraded_mode,
    )


def _make_piper(config: AdapterConfig, degraded_mode: bool = False) -> "TTSBackend":
    """Build the in-process Piper client (zero-setup default voice)."""
    from vocascade.tts.piper_client import DEFAULT_VOICE, PiperTTS

    return PiperTTS(
        voice=getattr(config, "tts_voice", "") or DEFAULT_VOICE,
        models_dir=getattr(config, "tts_models_dir", "") or "",
        degraded_mode=degraded_mode,
    )


REGISTRY: dict[str, Callable[..., "TTSBackend"]] = {
    "piper": _make_piper,
    "genie": _make_genie,
}


def make_tts_client(config: AdapterConfig, degraded_mode: bool = False) -> "TTSBackend":
    """Construct the configured TTS backend from ``REGISTRY``.

    Raises ValueError (fail fast at startup) for an unregistered backend name.
    """
    name = getattr(config, "tts_backend", "genie")
    try:
        factory = REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown TTS_BACKEND '{name}' (set in .env or the setup GUI: "
            f"python -m vocascade.setup_server). Registered backends: "
            f"{', '.join(sorted(REGISTRY))}"
        ) from None
    return factory(config, degraded_mode=degraded_mode)
