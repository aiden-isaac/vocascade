import base64
import logging
import os
import re
from pathlib import Path
from typing import AsyncIterator

import aiohttp


logger = logging.getLogger(__name__)

GENIE_SAMPLE_RATE = 32000
GENIE_CHANNELS = 1
GENIE_BYTES_PER_SAMPLE = 2
DEFAULT_CHARACTER_NAME = "fauna"
DEFAULT_REFERENCE_TEXT = "This is the reference audio for the Fauna voice."


class GenieTTSClient:
    def __init__(
        self,
        base_url: str | None = None,
        character_name: str | None = None,
        onnx_model_dir: str | Path | None = None,
        reference_audio_path: str | Path | None = None,
        reference_text: str | None = None,
        language: str | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        self.base_url = (base_url or os.getenv("GENIE_TTS_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.character_name = character_name or os.getenv("GENIE_CHARACTER_NAME") or DEFAULT_CHARACTER_NAME
        self.onnx_model_dir = str(
            onnx_model_dir
            or os.getenv("GENIE_ONNX_MODEL_DIR")
            or root / "genie_model_reference" / "genie-fauna" / "export"
        )
        self.reference_audio_path = str(
            reference_audio_path
            or os.getenv("GENIE_REFERENCE_AUDIO")
            or root / "genie_model_reference" / "fauna_ref.wav"
        )
        self.reference_text = reference_text or os.getenv("GENIE_REFERENCE_TEXT") or DEFAULT_REFERENCE_TEXT
        self.language = language or os.getenv("GENIE_LANGUAGE") or "en"
        self.initialized = False

    async def initialize(self) -> None:
        if self.initialized:
            return

        await self._post_json(
            "/load_character",
            {
                "character_name": self.character_name,
                "onnx_model_dir": self.onnx_model_dir,
                "language": self.language,
            },
        )
        await self._post_json(
            "/set_reference_audio",
            {
                "character_name": self.character_name,
                "audio_path": self.reference_audio_path,
                "audio_text": self.reference_text,
                "language": self.language,
            },
        )
        self.initialized = True
        logger.info("Initialized Genie TTS character '%s'", self.character_name)

    async def synthesize_pcm_chunks(self, text: str) -> AsyncIterator[bytes]:
        if not self.initialized:
            await self.initialize()

        payload = {
            "character_name": self.character_name,
            "text": text,
            "split_sentence": True,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/tts", json=payload) as response:
                if response.status != 200:
                    body = await response.text()
                    raise RuntimeError(f"Genie /tts failed with status {response.status}: {body}")

                buffer = bytearray()
                
                async for chunk in response.content.iter_chunked(4096):
                    if not chunk:
                        continue
                    buffer.extend(chunk)
                        
                    # Always yield chunks that are an exact multiple of 2 bytes
                    send_len = len(buffer) - (len(buffer) % 2)
                    if send_len > 0:
                        yield bytes(buffer[:send_len])
                        del buffer[:send_len]
                        
                if len(buffer) >= 2:
                    send_len = len(buffer) - (len(buffer) % 2)
                    yield bytes(buffer[:send_len])

    async def stop(self) -> None:
        try:
            await self._post_json("/stop", None)
        except Exception as error:
            logger.error("Failed to stop Genie TTS tasks: %s", error)

    async def _post_json(self, path: str, payload: dict | None) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}{path}", json=payload) as response:
                if response.status != 200:
                    body = await response.text()
                    raise RuntimeError(f"Genie {path} failed with status {response.status}: {body}")
                if response.content_type == "application/json":
                    return await response.json()
                return {}


def encode_pcm_chunk(chunk: bytes) -> str:
    return base64.b64encode(chunk).decode("ascii")


def iter_complete_sentences(chunks: list[str], next_text: str) -> tuple[list[str], list[str]]:
    buffer = "".join(chunks) + next_text
    sentences = re.findall(r".+?[.!?](?:\s+|$)", buffer, flags=re.DOTALL)
    if not sentences:
        return [], [buffer]

    consumed = sum(len(sentence) for sentence in sentences)
    remainder = buffer[consumed:]
    return [sentence.strip() for sentence in sentences if sentence.strip()], [remainder]
