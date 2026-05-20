"""
HTTP client for the Genie TTS server.
"""

import asyncio
import logging
from typing import AsyncIterator
import aiohttp

logger = logging.getLogger("voice_satellite.tts")

class GenieTTSClient:
    """
    Async HTTP client for Genie TTS server.
    Handles character registration, text synthesis streaming, input sanitisation,
    and graceful degradation on unreachable server.
    """
    def __init__(
        self,
        tts_url: str,
        character_name: str,
        onnx_model_dir: str | None = None,
        reference_audio: str | None = None,
        reference_text: str | None = None,
        language: str = "en",
        degraded_mode: bool = False
    ) -> None:
        self.tts_url = tts_url.rstrip("/")
        self.character_name = character_name
        self.onnx_model_dir = onnx_model_dir
        self.reference_audio = reference_audio
        self.reference_text = reference_text
        self.language = language
        self.degraded_mode = degraded_mode
        self.initialized = False

    async def load_character(self) -> None:
        """
        Registers the character ONNX model and reference audio with the Genie TTS server.
        """
        if self.degraded_mode or not self.onnx_model_dir:
            logger.warning("GenieTTSClient running in degraded mode (skipping character load)")
            self.degraded_mode = True
            return

        try:
            async with aiohttp.ClientSession() as session:
                # 1. Post to /load_character
                payload_load = {
                    "character_name": self.character_name,
                    "onnx_model_dir": self.onnx_model_dir,
                    "language": self.language
                }
                async with session.post(f"{self.tts_url}/load_character", json=payload_load, timeout=10) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Failed to load character: {body}. Entering degraded mode.")
                        self.degraded_mode = True
                        return

                # 2. Post to /set_reference_audio
                payload_ref = {
                    "character_name": self.character_name,
                    "audio_path": self.reference_audio,
                    "audio_text": self.reference_text,
                    "language": self.language
                }
                async with session.post(f"{self.tts_url}/set_reference_audio", json=payload_ref, timeout=10) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Failed to set reference audio: {body}. Entering degraded mode.")
                        self.degraded_mode = True
                        return

                self.initialized = True
                logger.info(f"Initialized Genie TTS character '{self.character_name}'")
        except Exception as e:
            logger.warning(f"Genie TTS server unreachable during character load: {e}. Degrading gracefully.")
            self.degraded_mode = True

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """
        Streams synthesized PCM bytes back from Genie TTS.
        Gracefully handles network errors and responds to cancellation.
        """
        # Input sanitisation
        text = text.strip()
        # 1. Skip non-alphanumeric
        if not any(c.isalnum() for c in text):
            return

        # 2. Ensure trailing punctuation
        if not text[-1] in (".", "!", "?"):
            text += "."

        # 3. Handle casing (convert to lowercase to prevent spelling out abbreviations/caps)
        text = text.lower()

        if self.degraded_mode:
            logger.warning(f"GenieTTSClient running in degraded mode — skipping synthesis for: '{text}'")
            return

        if not self.initialized:
            await self.load_character()

        if self.degraded_mode:
            return

        payload = {
            "character_name": self.character_name,
            "text": text,
            "split_sentence": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.tts_url}/tts", json=payload) as response:
                    if response.status != 200:
                        body = await response.text()
                        logger.error(f"Genie /tts failed with status {response.status}: {body}")
                        return

                    buffer = bytearray()
                    async for chunk in response.content.iter_chunked(4096):
                        if not chunk:
                            continue
                        buffer.extend(chunk)
                        
                        send_len = len(buffer) - (len(buffer) % 2)
                        if send_len > 0:
                            yield bytes(buffer[:send_len])
                            del buffer[:send_len]

                    if len(buffer) >= 2:
                        send_len = len(buffer) - (len(buffer) % 2)
                        yield bytes(buffer[:send_len])

        except asyncio.CancelledError:
            logger.info("GenieTTSClient synthesis task cancelled (barge-in)")
            raise
        except Exception as e:
            logger.warning(f"Genie TTS server unreachable during synthesis: {e}. Gracefully yielding empty audio.")

    async def ping_and_load(self) -> bool:
        """
        Verify connection and load the configured character.
        Used for startup verification.
        """
        await self.load_character()
        return not self.degraded_mode

    async def stop(self) -> None:
        """
        Sends a stop request to the Genie TTS server to abort any ongoing playback/synthesis.
        """
        if self.degraded_mode:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.tts_url}/stop", timeout=5) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Genie /stop failed with status {resp.status}: {body}")
        except Exception as e:
            logger.error(f"Failed to stop Genie TTS: {e}")
