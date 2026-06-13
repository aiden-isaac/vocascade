import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from vocascade.pipeline.pipeline import (
    VoicePipeline,
    PipelineStage,
    TextFrame,
    AudioFrame,
    InterruptionFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame
)
from vocascade.pipeline.tts import GenieTTSStage

class MockStage(PipelineStage):
    def __init__(self):
        super().__init__()
        self.received_frames = []

    async def push(self, frame):
        self.received_frames.append(frame)

class TestGenieTTSStage(unittest.IsolatedAsyncioTestCase):
    @patch("vocascade.pipeline.tts.GenieTTSClient")
    async def test_start_preloads_character(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.load_character = AsyncMock()
        mock_client_class.return_value = mock_client

        stage = GenieTTSStage(
            tts_url="http://localhost:8000",
            character_name="test-character"
        )
        
        await stage.start()
        mock_client.load_character.assert_called_once()
        self.assertTrue(stage._character_loaded)

    @patch("vocascade.pipeline.tts.GenieTTSClient")
    async def test_synthesis_success(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.load_character = AsyncMock()
        mock_client.stop = AsyncMock()
        mock_client.character_name = "test-character"
        
        async def mock_synthesize(text):
            yield b"\x01\x02"
            yield b"\x03\x04"
        mock_client.synthesize = mock_synthesize
        mock_client_class.return_value = mock_client

        stage = GenieTTSStage(
            tts_url="http://localhost:8000",
            character_name="test-character"
        )

        
        next_stage = MockStage()
        stage.next_stage = next_stage
        
        pipeline = VoicePipeline([stage, next_stage])
        await pipeline.start()
        
        await stage.push(TextFrame(text="Hello world"))
        await pipeline.stop()

        # Should receive:
        # 1. BotStartedSpeakingFrame
        # 2. AudioFrame (1)
        # 3. AudioFrame (2)
        # 4. BotStoppedSpeakingFrame
        frames = next_stage.received_frames
        self.assertEqual(len(frames), 4)
        self.assertIsInstance(frames[0], BotStartedSpeakingFrame)
        self.assertIsInstance(frames[1], AudioFrame)
        self.assertEqual(frames[1].audio, b"\x01\x02")
        self.assertIsInstance(frames[2], AudioFrame)
        self.assertEqual(frames[2].audio, b"\x03\x04")
        self.assertIsInstance(frames[3], BotStoppedSpeakingFrame)

    @patch("vocascade.pipeline.tts.GenieTTSClient")
    async def test_synthesis_sentinel_strip(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.load_character = AsyncMock()
        mock_client.stop = AsyncMock()
        mock_client_class.return_value = mock_client
        mock_client.synthesize = MagicMock() # if called, it's an error

        stage = GenieTTSStage(
            tts_url="http://localhost:8000",
            character_name="test-character"
        )
        
        next_stage = MockStage()
        stage.next_stage = next_stage
        
        pipeline = VoicePipeline([stage, next_stage])
        await pipeline.start()
        
        # Text only containing the sentinel should be stripped and not synthesize
        await stage.push(TextFrame(text="end session"))
        await pipeline.stop()

        self.assertEqual(len(next_stage.received_frames), 0)
        mock_client.synthesize.assert_not_called()

    @patch("vocascade.pipeline.tts.GenieTTSClient")
    async def test_synthesis_interrupted_by_event(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.load_character = AsyncMock()
        mock_client.character_name = "test-character"
        mock_client.stop = AsyncMock()
        mock_client_class.return_value = mock_client

        # Create a pipeline
        stage = GenieTTSStage(
            tts_url="http://localhost:8000",
            character_name="test-character"
        )
        next_stage = MockStage()
        stage.next_stage = next_stage
        pipeline = VoicePipeline([stage, next_stage])

        # Generator that triggers pipeline interrupt on first yield
        async def mock_synthesize(text):
            yield b"\x01\x02"
            pipeline.interrupt_event.set()
            yield b"\x03\x04"
            
        mock_client.synthesize = mock_synthesize

        await pipeline.start()
        await stage.push(TextFrame(text="Hello world"))
        await pipeline.stop()

        # Should only receive first AudioFrame before interrupt is checked
        frames = next_stage.received_frames
        self.assertGreaterEqual(len(frames), 2)
        self.assertIsInstance(frames[0], BotStartedSpeakingFrame)
        self.assertIsInstance(frames[1], AudioFrame)
        # Should call client.stop() on interrupt
        self.assertGreater(mock_client.stop.call_count, 0)
        # Should also ensure BotStoppedSpeakingFrame is sent in finally block
        self.assertIsInstance(frames[-1], BotStoppedSpeakingFrame)

    @patch("vocascade.pipeline.tts.GenieTTSClient")
    async def test_interruption_frame(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.load_character = AsyncMock()
        mock_client.stop = AsyncMock()
        mock_client_class.return_value = mock_client

        stage = GenieTTSStage(
            tts_url="http://localhost:8000",
            character_name="test-character"
        )
        next_stage = MockStage()
        stage.next_stage = next_stage
        
        await stage.push(InterruptionFrame())
        
        mock_client.stop.assert_called_once()
        self.assertEqual(len(next_stage.received_frames), 1)
        self.assertIsInstance(next_stage.received_frames[0], InterruptionFrame)

if __name__ == "__main__":
    unittest.main()

