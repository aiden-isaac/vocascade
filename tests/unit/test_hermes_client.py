import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import uuid
import json

from voice_satellite.gateway.hermes_client import HermesClient

class MockResponse:
    def __init__(self, lines):
        self.lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self.lines:
            yield line

class MockStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

class TestHermesClient(unittest.IsolatedAsyncioTestCase):
    async def test_init(self):
        client = HermesClient(base_url="http://localhost:8642/v1")
        self.assertEqual(client.base_url, "http://localhost:8642/v1")
        self.assertIsNone(client.session_id)
        self.assertIsNone(client.client)

    async def test_connect_and_close(self):
        client = HermesClient(base_url="http://localhost:8642/v1")
        await client.connect()
        self.assertIsNotNone(client.client)
        self.assertIsNotNone(client.session_id)
        
        # Verify valid UUID
        val = uuid.UUID(client.session_id)
        self.assertEqual(str(val), client.session_id)

        await client.close()
        self.assertIsNone(client.client)

    async def test_sessions_abort(self):
        client = HermesClient(base_url="http://localhost:8642/v1")
        await client.connect()
        first_session_id = client.session_id
        await client.sessions_abort()
        self.assertEqual(first_session_id, client.session_id)

    @patch("voice_satellite.gateway.hermes_client.httpx.AsyncClient")
    async def test_send_transcript_success(self, mock_async_client_cls):
        sse_lines = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            'data: {"choices": [{"delta": {"content": " world"}}]}',
            'data: [DONE]'
        ]
        
        mock_client = MagicMock()
        mock_response = MockResponse(sse_lines)
        mock_client.stream.return_value = MockStreamContext(mock_response)
        mock_async_client_cls.return_value = mock_client

        client = HermesClient(base_url="http://localhost:8642/v1")
        
        tokens = []
        async for token in client.send_transcript("Hi"):
            tokens.append(token)

        self.assertEqual(tokens, ["Hello", " world"])
        mock_client.stream.assert_called_once()
        args, kwargs = mock_client.stream.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "http://localhost:8642/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["X-Hermes-Session-Id"], client.session_id)
        self.assertEqual(kwargs["json"]["messages"], [{"role": "user", "content": "Hi"}])

    @patch("voice_satellite.gateway.hermes_client.httpx.AsyncClient")
    async def test_send_transcript_with_api_key(self, mock_async_client_cls):
        sse_lines = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            'data: [DONE]'
        ]
        
        mock_client = MagicMock()
        mock_response = MockResponse(sse_lines)
        mock_client.stream.return_value = MockStreamContext(mock_response)
        mock_async_client_cls.return_value = mock_client

        client = HermesClient(base_url="http://localhost:8642/v1", api_key="secret_token")
        
        tokens = []
        async for token in client.send_transcript("Hi"):
            tokens.append(token)

        self.assertEqual(tokens, ["Hello"])
        mock_client.stream.assert_called_once()
        args, kwargs = mock_client.stream.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret_token")
        self.assertEqual(kwargs["headers"]["X-Hermes-Session-Id"], client.session_id)

    @patch("voice_satellite.gateway.hermes_client.httpx.AsyncClient")
    @patch("voice_satellite.gateway.hermes_client.LatencyTracker")
    async def test_send_transcript_latency(self, mock_tracker_cls, mock_async_client_cls):
        sse_lines = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            'data: [DONE]'
        ]
        
        mock_client = MagicMock()
        mock_response = MockResponse(sse_lines)
        mock_client.stream.return_value = MockStreamContext(mock_response)
        mock_async_client_cls.return_value = mock_client

        mock_tracker = MagicMock()
        mock_tracker_cls.return_value = mock_tracker

        client = HermesClient(base_url="http://localhost:8642/v1")
        
        tokens = []
        async for token in client.send_transcript("Hi", session="sess456"):
            tokens.append(token)

        mock_tracker_cls.assert_called_once_with("llm_first_token", "sess456")
        mock_tracker.start.assert_called_once()
        mock_tracker.record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
