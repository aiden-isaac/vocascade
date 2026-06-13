"""
vocascade/transport/serializer.py — Standalone WebSocket JSON/binary codec.
Converts raw PCM audio bytes to/from AudioFrame, and JSON strings to/from ControlMessage.
"""

import json
import base64
import logging
from dataclasses import dataclass

logger = logging.getLogger("vocascade.transport.serializer")

@dataclass
class AudioFrame:
    audio: bytes
    sample_rate: int = 16000
    num_channels: int = 1

@dataclass
class ControlMessage:
    message: dict

class RawFrameSerializer:
    """
    Codec for vocascade WebSocket transport.
    Binary messages carry raw PCM audio.
    JSON messages carry control and status payloads.
    """

    def serialize(self, frame) -> str | bytes | None:
        """
        Serialize a frame into string (JSON) or bytes.
        """
        if isinstance(frame, AudioFrame):
            b64_audio = base64.b64encode(frame.audio).decode("utf-8")
            return json.dumps({
                "type": "audio",
                "data": b64_audio,
                "sample_rate": frame.sample_rate
            })
        elif isinstance(frame, ControlMessage):
            return json.dumps(frame.message)
        return None

    def deserialize(self, data: str | bytes) -> AudioFrame | ControlMessage | None:
        """
        Deserialize incoming WebSocket data into a frame.
        """
        if isinstance(data, bytes):
            return AudioFrame(
                audio=data,
                sample_rate=16000,
                num_channels=1
            )
        if isinstance(data, str):
            try:
                msg = json.loads(data)
                return ControlMessage(message=msg)
            except Exception as e:
                logger.error(f"Error parsing client JSON: {e}")
        return None
