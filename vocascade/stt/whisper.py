"""
Speech-to-Text Module wrapper using faster-whisper.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from faster_whisper import WhisperModel

from vocascade.telemetry import LatencyTracker

logger = logging.getLogger("vocascade.stt.whisper")

class WhisperSTT:
    """
    Wrapper around faster-whisper to handle voice transcribing on a background executor.
    Uses an asyncio.Lock to serialize transcription requests.
    """
    def __init__(self, model_name: str, language: str) -> None:
        self.model_name = model_name
        self.language = language
        self.lock = asyncio.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        
        logger.info(f"Loading faster-whisper model '{model_name}' on CPU")
        self.model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8"
        )
        logger.info(f"Loaded faster-whisper model '{model_name}'")

    async def transcribe(self, pcm_bytes: bytes, *, session: str = "") -> str:
        """
        Transcribes 16-bit PCM bytes to text asynchronously.
        Guarantees that inference runs on a background thread pool and that
        concurrent calls are serialized via a Lock.
        """
        if not pcm_bytes or len(pcm_bytes) < 2:
            return ""

        async with self.lock:
            tracker = LatencyTracker("stt", session)
            tracker.start()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self.executor,
                self._transcribe_sync,
                pcm_bytes
            )
            tracker.record()
            return result

    def _transcribe_sync(self, pcm_bytes: bytes) -> str:
        """Synchronous CPU transcription called on the executor thread."""
        audio = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        
        segments, _info = self.model.transcribe(
            audio,
            beam_size=1,
            language=self.language,
            vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt="A user asks a question or gives a command.",
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    def close(self) -> None:
        """Shutdown the background thread pool executor."""
        self.executor.shutdown(wait=False)
