"""
vocascade/pipeline/vad.py — Voice Activity Detection stage.
Supports authoritative Edge VAD (default) and server-side Silero VAD (optional).
"""

import logging
import numpy as np
from typing import Optional
from vocascade.pipeline.pipeline import (
    PipelineStage,
    Frame,
    AudioFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame
)

logger = logging.getLogger("vocascade.pipeline.vad")

class VADStage(PipelineStage):
    """
    VAD pipeline stage.
    If server_vad_enabled is True, processes raw continuous audio using server-side Silero VAD
    and injects UserStartedSpeakingFrame / UserStoppedSpeakingFrame.
    Otherwise, assumes edge VAD is authoritative and brackets each incoming AudioFrame.
    """

    def __init__(self, server_vad_enabled: bool = False, threshold: float = 0.5,
                 sample_rate: int = 16000, min_silence_duration_ms: int = 250,
                 speech_pad_ms: int = 50):
        super().__init__()
        self.server_vad_enabled = server_vad_enabled
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.min_silence_duration_ms = min_silence_duration_ms
        self.speech_pad_ms = speech_pad_ms

        self.model = None
        self.vad_iterator = None
        
        # Audio accumulator for server-side VAD
        # Silero v5 expects 512, 1024, or 1536 samples at 16kHz. We use 512 samples.
        self.chunk_samples = 512
        self.chunk_bytes = self.chunk_samples * 2  # 16-bit PCM (2 bytes per sample)
        self.audio_buffer = bytearray()
        
        self.is_speaking = False

    async def start(self):
        """Lazy load torch and silero-vad only if server-side VAD is enabled."""
        if self.server_vad_enabled:
            logger.info("Initializing server-side Silero VAD model...")
            try:
                import torch
                from silero_vad import load_silero_vad, VADIterator
                
                # Load Silero VAD model in CPU mode
                self.model = load_silero_vad()
                self.vad_iterator = VADIterator(
                    model=self.model,
                    threshold=self.threshold,
                    sampling_rate=self.sample_rate,
                    min_silence_duration_ms=self.min_silence_duration_ms,
                    speech_pad_ms=self.speech_pad_ms
                )
                self.audio_buffer.clear()
                self.is_speaking = False
                logger.info("Server-side Silero VAD initialized.")
            except Exception as e:
                logger.error(f"Failed to initialize Silero VAD: {e}. Falling back to Edge VAD mode.")
                self.server_vad_enabled = False

    async def stop(self):
        if self.vad_iterator:
            self.vad_iterator.reset_states()
        self.audio_buffer.clear()
        self.is_speaking = False

    async def push(self, frame: Frame):
        """Processes the frame and forwards VAD events / audio downstream."""
        if not isinstance(frame, AudioFrame):
            # Pass all non-audio frames downstream directly
            await super().push(frame)
            return

        if not self.server_vad_enabled:
            # Edge VAD Mode: each frame is a complete pre-segmented utterance
            logger.debug("Edge VAD processing single audio frame.")
            await super().push(UserStartedSpeakingFrame())
            await super().push(frame)
            await super().push(UserStoppedSpeakingFrame())
            return

        # Server VAD Mode: feed incoming stream to Silero VAD
        self.audio_buffer.extend(frame.audio)
        
        # Process in chunks of 512 samples
        while len(self.audio_buffer) >= self.chunk_bytes:
            chunk = self.audio_buffer[:self.chunk_bytes]
            del self.audio_buffer[:self.chunk_bytes]
            
            # Convert 16-bit PCM bytes to normalized float32 numpy array
            audio_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            
            # Run VAD Iterator (synchronous CPU inference offloaded or in-loop since it's light)
            # We wrap it in try-except to prevent crashes
            try:
                import torch
                audio_tensor = torch.from_numpy(audio_np)
                # VADIterator accepts tensor/array
                res = self.vad_iterator(audio_tensor)
            except Exception as e:
                logger.error(f"Error executing Silero VAD model: {e}")
                res = None
                
            if res is not None:
                if 'start' in res:
                    logger.info("Silero VAD: speech start detected.")
                    self.is_speaking = True
                    await super().push(UserStartedSpeakingFrame())
                elif 'end' in res:
                    logger.info("Silero VAD: speech end detected.")
                    self.is_speaking = False
                    await super().push(UserStoppedSpeakingFrame())
            
            # Forward the audio chunk if speaking
            if self.is_speaking:
                # Re-package the 512-sample chunk into an AudioFrame
                await super().push(AudioFrame(
                    audio=chunk,
                    sample_rate=self.sample_rate,
                    num_channels=frame.num_channels
                ))
