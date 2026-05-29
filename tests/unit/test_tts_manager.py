import asyncio
import unittest
from unittest.mock import MagicMock
from voice_satellite.tts.sentence_splitter import SentenceChunk
from voice_satellite.tts.manager import TTSTaskManager

class TestTTSTaskManager(unittest.IsolatedAsyncioTestCase):
    async def test_ordered_delivery_out_of_order_completion(self):
        manager = TTSTaskManager()
        
        # Create mock GenieTTSClient
        mock_client = MagicMock()
        
        # We want to simulate synthesis where task 0 completes slower than task 1.
        # Task 0 yields: chunk_0_A, chunk_0_B
        # Task 1 yields: chunk_1_A
        
        async def mock_synth_0(text, session=""):
            await asyncio.sleep(0.1) # Simulate slow TTS synthesis
            yield b"chunk_0_A"
            yield b"chunk_0_B"
            
        async def mock_synth_1(text, session=""):
            # Fast, yields immediately
            yield b"chunk_1_A"
            
        def side_effect(text, session=""):
            if text == "sentence zero":
                return mock_synth_0(text, session)
            else:
                return mock_synth_1(text, session)
                
        mock_client.synthesize.side_effect = side_effect
        
        chunk0 = SentenceChunk("sentence zero", False)
        chunk1 = SentenceChunk("sentence one", False)
        
        # Enqueue them
        manager.enqueue_tts(chunk0, mock_client, "session-123")
        manager.enqueue_tts(chunk1, mock_client, "session-123")
        manager.mark_complete()
        
        # Consume from manager
        results = []
        async for chunk, audio in manager.get_audio_chunks():
            results.append((chunk.text, audio))
            
        # Verify that even though sentence one was fast, sentence zero's chunks are yielded first!
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0], ("sentence zero", b"chunk_0_A"))
        self.assertEqual(results[1], ("sentence zero", b"chunk_0_B"))
        self.assertEqual(results[2], ("sentence one", b"chunk_1_A"))

    async def test_stop_cancels_pending_tasks(self):
        manager = TTSTaskManager()
        mock_client = MagicMock()
        
        task_started = asyncio.Event()
        task_cancelled = asyncio.Event()
        
        async def mock_synth(text, session=""):
            task_started.set()
            try:
                await asyncio.sleep(10) # Wait indefinitely
                yield b"never"
            except asyncio.CancelledError:
                task_cancelled.set()
                raise
                
        mock_client.synthesize.return_value = mock_synth("test")
        
        chunk = SentenceChunk("infinite loop", False)
        manager.enqueue_tts(chunk, mock_client, "session-123")
        
        # Wait for worker task to start
        await task_started.wait()
        
        # Stop the manager
        await manager.stop()
        
        # Verify that task was cancelled
        self.assertTrue(task_cancelled.is_set())
        self.assertEqual(len(manager._tasks), 0)
        self.assertEqual(len(manager._queues), 0)

if __name__ == "__main__":
    unittest.main()
