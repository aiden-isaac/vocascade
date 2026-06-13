"""
Genie TTS Service implementation for Pipecat.
Adapters the custom GenieTTSClient to Pipecat's TTSService interface.
"""

import logging
import re
from typing import AsyncGenerator
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
)
from vocascade.tts.genie_client import GenieTTSClient

logger = logging.getLogger("vocascade.tts_genie")

class GenieTTSService(TTSService):
    def __init__(
        self,
        tts_url: str,
        character_name: str,
        onnx_model_dir: str | None = None,
        reference_audio: str | None = None,
        reference_text: str | None = None,
        language: str = "en",
        degraded_mode: bool = False,
        sample_rate: int = 32000,
        **kwargs
    ):
        # We set push_text_frames=True because Genie TTS doesn't provide word timestamps.
        # This will make the parent class automatically emit TTSTextFrames for context sync.
        # Settings store mode wants every field initialized; Genie has a fixed
        # character/voice per server, so model/voice are None (unsupported).
        # Genie's first chunk routinely takes 4-7s; the default 3s audio-context
        # drain timeout would declare the context finished before any audio
        # arrives and drop the whole utterance.
        super().__init__(
            sample_rate=sample_rate,
            push_text_frames=True,
            settings=TTSSettings(model=None, voice=None, language=language),
            stop_frame_timeout_s=12.0,
            **kwargs
        )
        self.genie_sample_rate = sample_rate
        self._client = GenieTTSClient(
            tts_url=tts_url,
            character_name=character_name,
            onnx_model_dir=onnx_model_dir,
            reference_audio=reference_audio,
            reference_text=reference_text,
            language=language,
            degraded_mode=degraded_mode
        )
        self._character_loaded = False

    async def preload(self) -> None:
        """Register the Genie character ahead of pipeline start (app startup)."""
        if not self._character_loaded:
            logger.info("Initializing Genie TTS character...")
            await self._client.load_character()
            self._character_loaded = True

    async def close(self) -> None:
        """Close the HTTP client session (app shutdown)."""
        await self._client.close()

    async def start(self, frame: StartFrame) -> None:
        """Pipeline start. MUST call through to TTSService.start: it creates
        the serialization/audio-context drain task — without it the service
        swallows every queued frame (TTS audio and passthrough alike)."""
        await super().start(frame)
        await self.preload()

    async def stop(self, frame: EndFrame) -> None:
        """Pipeline stop (EndFrame). The HTTP session stays open for the next
        pipeline; the app closes it via close() on shutdown."""
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        await super().cancel(frame)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """
        Synthesize text to speech using Genie TTS server.
        Yields TTSAudioRawFrame chunks.
        """
        # Authoritative strip of the termination sentinel before it can be spoken.
        # Tolerant of casing and an optional space ("ENDSESSION", "end session", ...).
        text = re.sub(r"(?i)end\s*session", "", text).strip()
        if not text:
            yield None
            return

        logger.debug(f"GenieTTSService synthesizing: '{text}'")
        try:
            # First initialization if not already done
            if not self._character_loaded:
                await self.preload()

            await self.start_tts_usage_metrics(text)

            first_chunk = True
            async for chunk in self._client.synthesize(text):
                if first_chunk:
                    await self.stop_ttfb_metrics()
                    first_chunk = False
                
                if len(chunk) > 0:
                    yield TTSAudioRawFrame(
                        audio=chunk,
                        sample_rate=self.genie_sample_rate,
                        num_channels=1,
                        context_id=context_id
                    )

            # Signal successful completion
            yield None
        except Exception as e:
            logger.error(f"Error during Genie TTS synthesis: {e}", exc_info=True)
            yield ErrorFrame(error=f"Genie TTS error: {e}")
