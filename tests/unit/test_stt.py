import asyncio
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from voice_satellite.stt import WhisperSTT

class TestWhisperSTT(unittest.IsolatedAsyncioTestCase):
    @patch("voice_satellite.stt.whisper_stt.WhisperModel")
    def setUp(self, mock_whisper_model_cls):
        # Setup mocks
        self.mock_model = MagicMock()
        mock_whisper_model_cls.return_value = self.mock_model
        
        # Mock transcribe method returning a mock segment list
        self.mock_segment = MagicMock()
        self.mock_segment.text = "Hello world"
        self.mock_model.transcribe.return_value = ([self.mock_segment], None)
        
        self.stt = WhisperSTT(model_name="tiny.en", language="en")

    def tearDown(self):
        self.stt.close()

    async def test_whisper_stt_transcribe(self):
        # Perform transcription
        pcm = np.zeros(16000, dtype=np.int16).tobytes()
        result = await self.stt.transcribe(pcm)
        
        self.assertEqual(result, "Hello world")
        self.mock_model.transcribe.assert_called_once()
        
        # Verify the array shape passed to transcribe (16000 elements)
        args, kwargs = self.mock_model.transcribe.call_args
        self.assertEqual(len(args[0]), 16000)

    async def test_whisper_stt_empty_input(self):
        result_empty = await self.stt.transcribe(b"")
        self.assertEqual(result_empty, "")
        
        result_none = await self.stt.transcribe(None)
        self.assertEqual(result_none, "")

if __name__ == "__main__":
    unittest.main()
