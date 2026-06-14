"""
vocascade/pipeline/stt.py — Speech-to-Text pipeline stage.
"""

import asyncio
import logging
from vocascade.pipeline.pipeline import (
    PipelineStage,
    Frame,
    AudioFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    TranscriptionFrame,
    InterruptionFrame
)
from vocascade.stt.whisper import WhisperSTT

logger = logging.getLogger("vocascade.pipeline.stt")

class STTStage(PipelineStage):
    """
    STT pipeline stage.
    Accumulates incoming AudioFrame chunks between UserStartedSpeakingFrame
    and UserStoppedSpeakingFrame. Upon receiving UserStoppedSpeakingFrame,
    transcribes the accumulated audio using WhisperSTT and pushes a TranscriptionFrame.
    """
    def __init__(self, whisper_stt: WhisperSTT):
        super().__init__()
        self.stt = whisper_stt
        self.audio_buffer = bytearray()
        self.is_speaking = False
        self._transcribe_task = None

    async def push(self, frame: Frame):
        if isinstance(frame, UserStartedSpeakingFrame):
            logger.info("STTStage: user started speaking. Clearing buffer.")
            self.is_speaking = True
            self.audio_buffer.clear()
            if self._transcribe_task and not self._transcribe_task.done():
                self._transcribe_task.cancel()
            await super().push(frame)

        elif isinstance(frame, AudioFrame):
            if self.is_speaking:
                self.audio_buffer.extend(frame.audio)
            # Consume user audio frame without forwarding downstream to speaker
            return

        elif isinstance(frame, UserStoppedSpeakingFrame):
            if self.is_speaking:
                self.is_speaking = False
                audio_bytes = bytes(self.audio_buffer)
                self.audio_buffer.clear()
                
                # Start transcription task
                self._transcribe_task = asyncio.create_task(self._transcribe(audio_bytes))
            await super().push(frame)

        elif isinstance(frame, InterruptionFrame):
            logger.info("STTStage: received InterruptionFrame, cancelling active transcription.")
            self.is_speaking = False
            self.audio_buffer.clear()
            if self._transcribe_task and not self._transcribe_task.done():
                self._transcribe_task.cancel()
            await super().push(frame)

        else:
            await super().push(frame)

    async def _transcribe(self, audio_bytes: bytes):
        try:
            session_id = getattr(self.pipeline, "session_id", "") if self.pipeline else ""
            text = await self.stt.transcribe(audio_bytes, session=session_id)
            text = text.strip()
            logger.info(f"STTStage transcribed: '{text}'")
            if text:
                await super().push(TranscriptionFrame(text=text))
        except asyncio.CancelledError:
            logger.info("STTStage: transcription task cancelled.")
        except Exception as e:
            logger.error(f"STTStage error during transcription: {e}", exc_info=True)
