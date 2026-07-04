"""
vocascade/pipeline/tts.py — Text-to-Speech stage.
Wraps GenieTTSClient and applies resolved character audio effects.
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
from vocascade.tts.genie_client import GenieTTSClient
from vocascade.audio.effects import apply_effect_chain, apply_gain, get_character_effects_config

logger = logging.getLogger("vocascade.pipeline.tts")

class GenieTTSStage(PipelineStage):
    """
    TTS pipeline stage.
    Synthesizes incoming TextFrame content using Genie TTS,
    applies character effects, and pushes AudioFrame chunks downstream.
    """

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
        volume: float = 1.0
    ):
        super().__init__()
        self.tts_url = tts_url
        self.character_name = character_name
        self.sample_rate = sample_rate
        self.volume = volume
        
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

    async def start(self):
        """Preloads the character model on pipeline start."""
        if not self._character_loaded:
            logger.info(f"GenieTTSStage preloading character '{self.character_name}'...")
            await self._client.load_character()
            self._character_loaded = True

    async def warmup(self):
        """Preload the character and force Genie to load its synthesis models
        (CN_HuBERT + speaker verification) with one throwaway synth, so the first
        real reply doesn't pay the ~8s cold start. Best-effort; never raises."""
        if self._client.degraded_mode:
            return
        try:
            await self.start()                       # POST /load_character
            async for _ in self._client.synthesize("Ready."):
                pass                                  # discard audio; just warm the models
            logger.info("GenieTTSStage warmup complete — TTS models preloaded.")
        except Exception as e:
            logger.warning("GenieTTSStage warmup failed (non-fatal): %s", e)

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
                logger.info("GenieTTSStage: pipeline is interrupted, skipping synthesis.")
                return

            # Clean/strip the sentinel termination string
            text = re.sub(r"(?i)end\s*session", "", frame.text).strip()
            if not text:
                return

            logger.info(f"GenieTTSStage synthesizing text: '{text}'")
            
            # Fire BotStartedSpeakingFrame downstream
            await super().push(BotStartedSpeakingFrame())

            # Get randomized character effects config if applicable
            effects_config = get_character_effects_config(self.character_name)

            try:
                # Stream PCM chunks from client
                async for chunk in self._client.synthesize(text):
                    # Check for barge-in / interrupt during streaming
                    if self.pipeline and self.pipeline.interrupt_event.is_set():
                        logger.info("GenieTTSStage: synthesis interrupted by pipeline event!")
                        await self._client.stop()
                        break

                    if chunk:
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
                logger.info("GenieTTSStage: synthesis task cancelled.")
                await self._client.stop()
                raise
            except Exception as e:
                logger.error(f"GenieTTSStage error during synthesis: {e}", exc_info=True)
            finally:
                # Fire BotStoppedSpeakingFrame downstream
                await super().push(BotStoppedSpeakingFrame())

        elif isinstance(frame, InterruptionFrame):
            logger.info("GenieTTSStage received InterruptionFrame. Stopping synthesis.")
            await self._client.stop()
            await super().push(frame)
        else:
            # Pass through all other frames
            await super().push(frame)
