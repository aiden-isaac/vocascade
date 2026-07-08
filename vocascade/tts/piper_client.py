"""
vocascade/tts/piper_client.py — Piper TTS backend (the zero-setup default voice).

In-process, CPU-only neural TTS via the ``piper`` package. Conforms to the
``TTSBackend`` protocol: streamed mono s16le PCM at the voice's native sample
rate. Voices are ``<voice_id>.onnx`` + ``.json`` files in a models dir; the
stock voices are auto-downloaded on first start (best-effort), after which the
client degrades to text-only with a located message on any failure.
"""

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

from vocascade.telemetry import LatencyTracker

logger = logging.getLogger("vocascade.tts")

DEFAULT_MODELS_DIR = "~/.local/share/vocascade/piper"
# Friendly names for the shipped default voices; any other piper voice id
# (e.g. "en_GB-alan-medium") is used verbatim.
STOCK_VOICES = {
    "female": "en_US-lessac-medium",
    "male": "en_US-ryan-medium",
}
DEFAULT_VOICE = "female"
DOWNLOAD_TIMEOUT_S = 180.0  # one ~60MB fetch; past this we degrade

# ponytail: module-level voice cache — clients are built per WS session but the
# ONNX model load (~1s) should happen once per process. Keyed by resolved path.
_LOADED_VOICES: dict[str, object] = {}


class PiperTTS:
    """
    Piper TTS backend. Loads a voice from ``models_dir`` (downloading a stock
    voice on first use), then synthesizes off the event loop via
    ``asyncio.to_thread``. ``degraded_mode`` is set instead of raising.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        models_dir: str = DEFAULT_MODELS_DIR,
        degraded_mode: bool = False,
    ) -> None:
        self.voice_id = STOCK_VOICES.get(voice, voice)
        self.models_dir = Path(models_dir or DEFAULT_MODELS_DIR).expanduser()
        self.degraded_mode = degraded_mode
        self.sample_rate = 22050  # replaced by the voice's declared rate on start()
        self.initialized = False
        self._voice = None
        self._stop_requested = False

    def model_path(self) -> Path:
        """Resolved path of the voice's .onnx file (its .json sits beside it)."""
        return self.models_dir / f"{self.voice_id}.onnx"

    async def start(self) -> None:
        """Load the configured voice, downloading a missing one once
        (best-effort, bounded). Sets ``degraded_mode`` on failure; never raises."""
        if self.degraded_mode or self.initialized:
            return

        model_path = self.model_path()
        if not model_path.exists():
            try:
                from piper.download_voices import download_voice
                self.models_dir.mkdir(parents=True, exist_ok=True)
                logger.info("Piper voice '%s' not found — downloading to %s ...",
                            self.voice_id, self.models_dir)
                await asyncio.wait_for(
                    asyncio.to_thread(download_voice, self.voice_id, self.models_dir),
                    timeout=DOWNLOAD_TIMEOUT_S,
                )
            except Exception as e:
                logger.warning(
                    "Piper voice '%s' is missing from %s and could not be downloaded "
                    "(%s). Entering degraded mode (text replies only). Install it with: "
                    ".venv/bin/python -m piper.download_voices %s --data-dir %s",
                    self.voice_id, self.models_dir, e, self.voice_id, self.models_dir,
                )
                self.degraded_mode = True
                return

        try:
            cached = _LOADED_VOICES.get(str(model_path))
            if cached is None:
                from piper import PiperVoice
                cached = await asyncio.to_thread(PiperVoice.load, model_path)
                _LOADED_VOICES[str(model_path)] = cached
            self._voice = cached
            self.sample_rate = self._voice.config.sample_rate
            self.initialized = True
            logger.info("Initialized Piper voice '%s' (%d Hz)", self.voice_id, self.sample_rate)
        except Exception as e:
            logger.warning("Failed to load Piper voice '%s' from %s: %s. "
                           "Entering degraded mode.", self.voice_id, model_path, e)
            self.degraded_mode = True

    async def synthesize(self, text: str, *, session: str = "") -> AsyncIterator[bytes]:
        """
        Streams synthesized mono s16le PCM for ``text`` at ``self.sample_rate``.
        Synthesis runs in a worker thread; chunks are yielded per piper sentence.
        """
        text = text.strip()
        if not any(c.isalnum() for c in text):
            return

        if not self.initialized:
            await self.start()
        if self.degraded_mode:
            logger.warning("PiperTTS in degraded mode — skipping synthesis for: '%s'", text)
            return

        self._stop_requested = False
        tts_tracker = LatencyTracker("tts_first_chunk", session)
        tts_tracker.start()
        first_chunk_recorded = False

        # ponytail: whole-segment synth in one to_thread call — conveyor segments
        # are <=220 chars (~1-2 sentences), so this stays sub-second on CPU.
        # Switch to a queue-streaming worker if first-chunk latency ever matters.
        def _synth() -> list[bytes]:
            return [chunk.audio_int16_bytes for chunk in self._voice.synthesize(text)]

        try:
            chunks = await asyncio.to_thread(_synth)
        except asyncio.CancelledError:
            logger.info("PiperTTS synthesis task cancelled (barge-in)")
            raise
        except Exception as e:
            logger.warning("Piper synthesis failed: %s. Entering degraded mode.", e)
            self.degraded_mode = True
            return

        for chunk in chunks:
            if self._stop_requested:
                return
            if not chunk:
                continue
            if not first_chunk_recorded:
                tts_tracker.record()
                first_chunk_recorded = True
            yield chunk

    async def stop(self) -> None:
        """Aborts an in-flight synthesis stream (barge-in): remaining chunks are dropped."""
        self._stop_requested = True

    async def close(self) -> None:
        """No per-client resources; loaded voices stay cached for the process."""
        return None
