import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from pipecat.frames.frames import TTSAudioRawFrame, ErrorFrame
from voice_adapter.tts_genie import GenieTTSService


@pytest.mark.asyncio
async def test_genie_tts_service_yields_audio():
    service = GenieTTSService(
        tts_url="http://fake",
        character_name="test",
        onnx_model_dir="/model",
        reference_audio="/audio",
        reference_text="text",
        degraded_mode=False
    )
    # Set internal sample rate directly to simulate pipeline start
    service._sample_rate = 32000
    service._client.load_character = AsyncMock()
    
    async def mock_synthesize(text, *args, **kwargs):
        yield b"chunk1"
        yield b"chunk2"
    service._client.synthesize = mock_synthesize
    
    frames = []
    async for frame in service.run_tts("Hello world", "ctx_1"):
        frames.append(frame)
        
    assert len(frames) == 3
    assert isinstance(frames[0], TTSAudioRawFrame)
    assert frames[0].audio == b"chunk1"
    assert frames[0].sample_rate == 32000
    assert frames[0].num_channels == 1
    assert frames[0].context_id == "ctx_1"
    assert isinstance(frames[1], TTSAudioRawFrame)
    assert frames[1].audio == b"chunk2"
    assert frames[2] is None


@pytest.mark.asyncio
async def test_genie_tts_service_degraded_mode():
    service = GenieTTSService(
        tts_url="http://fake",
        character_name="test",
        degraded_mode=True
    )
    service._sample_rate = 32000
    
    async def mock_synthesize(text, *args, **kwargs):
        if service._client.degraded_mode:
            return
        yield b"audio"
    service._client.synthesize = mock_synthesize
    
    frames = []
    async for frame in service.run_tts("Hello world", "ctx_1"):
        frames.append(frame)
        
    assert len(frames) == 1
    assert frames[0] is None


@pytest.mark.asyncio
async def test_genie_tts_service_error_handling():
    service = GenieTTSService(
        tts_url="http://fake",
        character_name="test"
    )
    service._sample_rate = 32000
    service._client.load_character = AsyncMock()
    
    async def mock_synthesize(text, *args, **kwargs):
        raise RuntimeError("Genie error")
        yield b"" # Generator format
    service._client.synthesize = mock_synthesize
    
    frames = []
    async for frame in service.run_tts("Hello world", "ctx_1"):
        frames.append(frame)
        
    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)
    assert "Genie TTS error" in frames[0].error
