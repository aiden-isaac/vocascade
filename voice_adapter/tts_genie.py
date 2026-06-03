"""
Genie TTS Service implementation for Pipecat.
Adapters the custom GenieTTSClient to Pipecat's TTSService interface.
"""

import logging
from typing import AsyncGenerator
from pipecat.services.tts_service import TTSService
from pipecat.frames.frames import Frame, TTSAudioRawFrame, ErrorFrame
from voice_satellite.tts.genie_client import GenieTTSClient

logger = logging.getLogger("voice_adapter.tts_genie")

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
        super().__init__(
            sample_rate=sample_rate,
            push_text_frames=True,
            **kwargs
        )
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

    async def start(self) -> None:
        """Initializes the Genie TTS character registration."""
        if not self._character_loaded:
            logger.info("Initializing Genie TTS character...")
            await self._client.load_character()
            self._character_loaded = True

    async def stop(self) -> None:
        """Closes the client session."""
        await self._client.close()

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """
        Synthesize text to speech using Genie TTS server.
        Yields TTSAudioRawFrame chunks.
        """
        logger.debug(f"GenieTTSService synthesizing: '{text}'")
        try:
            # First initialization if not already done
            if not self._character_loaded:
                await self.start()

            await self.start_tts_usage_metrics(text)

            first_chunk = True
            async for chunk in self._client.synthesize(text):
                if first_chunk:
                    await self.stop_ttfb_metrics()
                    first_chunk = False
                
                if len(chunk) > 0:
                    yield TTSAudioRawFrame(
                        audio=chunk,
                        sample_rate=self.sample_rate,
                        num_channels=1,
                        context_id=context_id
                    )

            # Signal successful completion
            yield None
        except Exception as e:
            logger.error(f"Error during Genie TTS synthesis: {e}", exc_info=True)
            yield ErrorFrame(error=f"Genie TTS error: {e}")
