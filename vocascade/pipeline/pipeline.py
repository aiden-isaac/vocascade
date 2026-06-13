"""
vocascade/pipeline/pipeline.py — Custom asyncio voice pipeline core.
Defines base Frame classes, PipelineStage, and the VoicePipeline orchestrator.
"""

import asyncio
import logging
from typing import List, Optional

logger = logging.getLogger("vocascade.pipeline")

class Frame:
    """Base class for all data and events flowing through the pipeline."""
    pass

class AudioFrame(Frame):
    """Carries raw 16-bit PCM audio bytes."""
    def __init__(self, audio: bytes, sample_rate: int = 16000, num_channels: int = 1):
        self.audio = audio
        self.sample_rate = sample_rate
        self.num_channels = num_channels

class UserStartedSpeakingFrame(Frame):
    """Fired when VAD detects the user started speaking."""
    pass

class UserStoppedSpeakingFrame(Frame):
    """Fired when VAD detects the user stopped speaking."""
    pass

class BotStartedSpeakingFrame(Frame):
    """Fired when TTS/speaker starts speaking."""
    pass

class BotStoppedSpeakingFrame(Frame):
    """Fired when TTS/speaker stops speaking."""
    pass

class TranscriptionFrame(Frame):
    """Carries transcribed text from STT stage."""
    def __init__(self, text: str):
        self.text = text

class TextFrame(Frame):
    """Carries textual response from the routing/LLM stage."""
    def __init__(self, text: str):
        self.text = text

class InterruptionFrame(Frame):
    """Fired when a user barge-in / interruption is detected."""
    pass

class ControlMessageFrame(Frame):
    """Carries a control/status JSON message for the transport/client."""
    def __init__(self, message: dict):
        self.message = message

class PipelineStage:
    """Base class for a stage in the VoicePipeline."""
    def __init__(self):
        self.next_stage: Optional[PipelineStage] = None
        self.pipeline: Optional['VoicePipeline'] = None

    async def push(self, frame: Frame):
        """
        Receives a frame, processes it, and pushes to the next stage.
        Subclasses should override this method to implement processing logic.
        """
        if self.next_stage:
            await self.next_stage.push(frame)

    async def start(self):
        """Hook called when the pipeline starts up."""
        pass

    async def stop(self):
        """Hook called when the pipeline shuts down."""
        pass


class VoicePipeline:
    """
    Orchestrates the voice loop stage-by-stage without using a framework.
    Manages stage linking and a shared asyncio.Event for barge-in / STOP interrupts.
    """
    def __init__(self, stages: List[PipelineStage]):
        self.stages = stages
        self.interrupt_event = asyncio.Event()
        
        # Link stages sequentially
        for i in range(len(stages) - 1):
            stages[i].next_stage = stages[i + 1]
        for stage in stages:
            stage.pipeline = self

    async def push(self, frame: Frame):
        """Injects a frame into the first stage of the pipeline."""
        if self.stages:
            await self.stages[0].push(frame)

    async def start(self):
        """Starts all pipeline stages and clears the interrupt event."""
        logger.info("Starting VoicePipeline...")
        self.interrupt_event.clear()
        for stage in self.stages:
            await stage.start()

    async def stop(self):
        """Stops all pipeline stages."""
        logger.info("Stopping VoicePipeline...")
        for stage in self.stages:
            await stage.stop()
        self.interrupt_event.set()
