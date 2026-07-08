"""
vocascade/pipeline/tts.py — Text-to-Speech stage.
Backend-agnostic: synthesizes TextFrames through an injected ``TTSBackend``
client (vocascade/tts/protocol.py) and applies resolved character effects.
"""

import asyncio
import logging
import re
from vocascade.pipeline.pipeline import (
    PipelineStage,
    Frame,
    TextFrame,
    AudioFrame,
    InterruptionFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame
)
from vocascade.tts.protocol import TTSBackend
from vocascade.audio.effects import (
    apply_effect_chain,
    apply_gain,
    get_character_effects_config,
    resample_pcm,
)

logger = logging.getLogger("vocascade.pipeline.tts")

class TTSStage(PipelineStage):
    """
    TTS pipeline stage.
    Synthesizes incoming TextFrame content through the injected TTS backend,
    applies character effects, and pushes AudioFrame chunks downstream.
    """

    def __init__(
        self,
        client: TTSBackend,
        out_sample_rate: int = 32000,
        volume: float = 1.0,
        voice_name: str = "default",
    ):
        super().__init__()
        self._client = client
        self.sample_rate = out_sample_rate
        self.volume = volume
        self.voice_name = voice_name
        self._voice_loaded = False

    async def start(self):
        """Preloads the backend voice on pipeline start."""
        if not self._voice_loaded:
            logger.info(f"TTSStage preloading voice '{self.voice_name}'...")
            await self._client.start()
            self._voice_loaded = True

    async def warmup(self):
        """Preload the voice and force the backend to load its synthesis models
        with one throwaway synth, so the first real reply doesn't pay the cold
        start (~8s for Genie's CN_HuBERT + speaker verification). Best-effort;
        never raises."""
        if self._client.degraded_mode:
            return
        try:
            await self.start()                       # load the configured voice
            async for _ in self._client.synthesize("Ready."):
                pass                                  # discard audio; just warm the models
            logger.info("TTSStage warmup complete — TTS models preloaded.")
        except Exception as e:
            logger.warning("TTSStage warmup failed (non-fatal): %s", e)

    async def stop(self):
        """Sends stop request to abort active playback/synthesis."""
        await self._client.stop()

    async def close(self):
        """Closes the client connection session."""
        await self._client.close()

    async def push(self, frame: Frame):
        """Processes TextFrame synthesis and handles interruption events."""
        if isinstance(frame, TextFrame):
            # Authoritative check: do not synthesize if pipeline is already interrupted
            if self.pipeline and self.pipeline.interrupt_event.is_set():
                logger.info("TTSStage: pipeline is interrupted, skipping synthesis.")
                return

            # Clean/strip the sentinel termination string
            text = re.sub(r"(?i)end\s*session", "", frame.text).strip()
            if not text:
                return

            logger.info(f"TTSStage synthesizing text: '{text}'")

            # Fire BotStartedSpeakingFrame downstream
            await super().push(BotStartedSpeakingFrame())

            # Get randomized character effects config if applicable
            effects_config = get_character_effects_config(self.voice_name)

            try:
                # Stream PCM chunks from client
                async for chunk in self._client.synthesize(text):
                    # Check for barge-in / interrupt during streaming
                    if self.pipeline and self.pipeline.interrupt_event.is_set():
                        logger.info("TTSStage: synthesis interrupted by pipeline event!")
                        await self._client.stop()
                        break

                    if chunk:
                        # Normalize the backend's native rate to the wire rate
                        # first — effects assume TTS_SAMPLE_RATE-domain audio.
                        src_rate = self._client.sample_rate
                        if src_rate != self.sample_rate:
                            chunk = resample_pcm(chunk, src_rate, self.sample_rate)
                        # Apply effects chain if character effects configured
                        if effects_config:
                            chunk = apply_effect_chain(chunk, effects_config)
                        if self.volume != 1.0:
                            chunk = apply_gain(chunk, self.volume)

                        if chunk:
                            await super().push(AudioFrame(
                                audio=chunk,
                                sample_rate=self.sample_rate,
                                num_channels=1
                            ))

            except asyncio.CancelledError:
                logger.info("TTSStage: synthesis task cancelled.")
                await self._client.stop()
                raise
            except Exception as e:
                logger.error(f"TTSStage error during synthesis: {e}", exc_info=True)
            finally:
                # Fire BotStoppedSpeakingFrame downstream
                await super().push(BotStoppedSpeakingFrame())

        elif isinstance(frame, InterruptionFrame):
            logger.info("TTSStage received InterruptionFrame. Stopping synthesis.")
            await self._client.stop()
            await super().push(frame)
        else:
            # Pass through all other frames
            await super().push(frame)
