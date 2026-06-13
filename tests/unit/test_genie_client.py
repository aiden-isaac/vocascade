import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from vocascade.tts.genie_client import GenieTTSClient

class TestGenieTTSClient(unittest.IsolatedAsyncioTestCase):
    @patch("vocascade.tts.genie_client.aiohttp.ClientSession")
    async def test_load_character_success(self, mock_session_cls):
        # Setup session mock
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session_cls.return_value = mock_session
        
        # Setup post mock
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_session.post.return_value.__aenter__.return_value = mock_response

        client = GenieTTSClient(
            tts_url="http://localhost:8000",
            character_name="default",
            onnx_model_dir="/path/to/onnx",
            reference_audio="/path/to/ref.wav",
            reference_text="Ref text"
        )
        
        await client.load_character()
        
        self.assertTrue(client.initialized)
        self.assertFalse(client.degraded_mode)
        # Check load_character and set_reference_audio post calls
        self.assertEqual(mock_session.post.call_count, 2)

    @patch("vocascade.tts.genie_client.aiohttp.ClientSession")
    async def test_load_character_failure(self, mock_session_cls):
        # Setup session mock
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session_cls.return_value = mock_session
        
        # Setup post mock returning 500
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        mock_session.post.return_value.__aenter__.return_value = mock_response

        client = GenieTTSClient(
            tts_url="http://localhost:8000",
            character_name="default",
            onnx_model_dir="/path/to/onnx",
            reference_audio="/path/to/ref.wav",
            reference_text="Ref text"
        )
        
        await client.load_character()
        
        self.assertFalse(client.initialized)
        self.assertTrue(client.degraded_mode)

    @patch("vocascade.tts.genie_client.aiohttp.ClientSession")
    async def test_synthesize_success(self, mock_session_cls):
        # Setup session mock
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session_cls.return_value = mock_session
        
        # Setup post mock for /tts
        mock_response = AsyncMock()
        mock_response.status = 200
        
        # Mock streaming content iterator
        async def mock_iter_chunked(chunk_size):
            yield b"\x01\x02\x03\x04"
            yield b"\x05\x06"
        mock_response.content.iter_chunked = mock_iter_chunked
        mock_session.post.return_value.__aenter__.return_value = mock_response

        client = GenieTTSClient(
            tts_url="http://localhost:8000",
            character_name="default",
            onnx_model_dir="/path/to/onnx",
            reference_audio="/path/to/ref.wav",
            reference_text="Ref text"
        )
        # Pretend it was already initialized so it doesn't call load_character again
        client.initialized = True
        
        chunks = []
        async for chunk in client.synthesize("Hello world"):
            chunks.append(chunk)
            
        self.assertEqual(b"".join(chunks), b"\x01\x02\x03\x04\x05\x06")

    async def test_input_sanitisation(self):
        client = GenieTTSClient(
            tts_url="http://localhost:8000",
            character_name="default",
            onnx_model_dir=None  # Degraded mode
        )
        
        # Non-alphanumeric should return nothing
        chunks = []
        async for chunk in client.synthesize("!!!"):
            chunks.append(chunk)
        self.assertEqual(chunks, [])

    @patch("vocascade.tts.genie_client.aiohttp.ClientSession")
    async def test_synthesize_unreachable_server(self, mock_session_cls):
        # Mock a connection error / exception
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session_cls.return_value = mock_session
        mock_session.post.side_effect = Exception("Connection refused")

        client = GenieTTSClient(
            tts_url="http://localhost:8000",
            character_name="default",
            onnx_model_dir="/path/to/onnx",
            reference_audio="/path/to/ref.wav",
            reference_text="Ref text"
        )
        client.initialized = True

        # Should yield nothing and not raise exception
        chunks = []
        async for chunk in client.synthesize("Hello world"):
            chunks.append(chunk)
        self.assertEqual(chunks, [])
        self.assertTrue(client.degraded_mode)
        self.assertFalse(client.initialized)

    @patch("vocascade.tts.genie_client.aiohttp.ClientSession")
    async def test_close_session(self, mock_session_cls):
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session_cls.return_value = mock_session

        client = GenieTTSClient(
            tts_url="http://localhost:8000",
            character_name="default"
        )
        client._get_session()
        await client.close()
        mock_session.close.assert_called_once()
        self.assertIsNone(client._session)

    @patch("vocascade.tts.genie_client.aiohttp.ClientSession")
    @patch("vocascade.tts.genie_client.LatencyTracker")
    async def test_synthesize_latency(self, mock_tracker_cls, mock_session_cls):
        mock_response = AsyncMock()
        mock_response.status = 200
        async def mock_iter_chunked(n):
            yield b"audio_ch"
        mock_response.content.iter_chunked = mock_iter_chunked

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session_cls.return_value = mock_session
        mock_session.post.return_value.__aenter__.return_value = mock_response

        client = GenieTTSClient(
            tts_url="http://localhost:8000",
            character_name="default"
        )
        client.initialized = True

        mock_tracker = MagicMock()
        mock_tracker_cls.return_value = mock_tracker

        chunks = []
        async for chunk in client.synthesize("Hello world", session="sess999"):
            chunks.append(chunk)

        self.assertEqual(chunks, [b"audio_ch"])
        mock_tracker_cls.assert_called_once_with("tts_first_chunk", "sess999")
        mock_tracker.start.assert_called_once()
        mock_tracker.record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
