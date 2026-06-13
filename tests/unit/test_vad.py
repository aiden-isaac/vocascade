import unittest
from unittest.mock import MagicMock, patch
from vocascade.pipeline.pipeline import (
    AudioFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame
)
from vocascade.pipeline.vad import VADStage

class MockStage:
    def __init__(self):
        self.received_frames = []

    async def push(self, frame):
        self.received_frames.append(frame)

class TestVADStage(unittest.TestCase):
    async def _run_async(self, coro):
        import asyncio
        return await coro

    def test_edge_vad_mode(self):
        # Edge VAD mode should bracket any incoming AudioFrame with Start/Stop speaking frames
        stage = VADStage(server_vad_enabled=False)
        next_stage = MockStage()
        stage.next_stage = next_stage

        import asyncio
        async def run():
            await stage.start()
            frame = AudioFrame(audio=b"1234" * 100)
            await stage.push(frame)
            await stage.stop()

        asyncio.run(run())

        self.assertEqual(len(next_stage.received_frames), 3)
        self.assertIsInstance(next_stage.received_frames[0], UserStartedSpeakingFrame)
        self.assertIsInstance(next_stage.received_frames[1], AudioFrame)
        self.assertEqual(next_stage.received_frames[1].audio, b"1234" * 100)
        self.assertIsInstance(next_stage.received_frames[2], UserStoppedSpeakingFrame)

    @patch("silero_vad.load_silero_vad")
    @patch("silero_vad.VADIterator")
    def test_server_vad_mode_triggered(self, mock_vad_iterator_class, mock_load_silero):
        # Configure Mock VADIterator
        mock_iterator = MagicMock()
        mock_vad_iterator_class.return_value = mock_iterator

        # Simulate: first call returns None, second returns start=True, third returns None, fourth returns end=True
        mock_iterator.side_effect = [
            None,
            {"start": 1000},
            None,
            {"end": 2000}
        ]

        stage = VADStage(server_vad_enabled=True)
        next_stage = MockStage()
        stage.next_stage = next_stage

        import asyncio
        async def run():
            await stage.start()
            
            # Send 4 chunks of 512 samples (1024 bytes each)
            # Total 4096 bytes
            for i in range(4):
                frame = AudioFrame(audio=b"\x00\x00" * 512)
                await stage.push(frame)
                
            await stage.stop()

        asyncio.run(run())

        # Check call counts
        self.assertEqual(mock_iterator.call_count, 4)

        # Frames should have:
        # 1. UserStartedSpeakingFrame (triggered on 2nd chunk)
        # 2. AudioFrame (2nd chunk audio)
        # 3. AudioFrame (3rd chunk audio, is_speaking remains True)
        # 4. UserStoppedSpeakingFrame (triggered on 4th chunk end)
        # 5. AudioFrame (4th chunk audio? VADStage pushes audio chunk if is_speaking is True.
        #    Wait! If res has 'end', is_speaking was True before, it pushes the audio frame, then sets is_speaking=False.
        #    Let's check if the 4th chunk audio was pushed before or after UserStoppedSpeakingFrame.)
        
        frames = next_stage.received_frames
        self.assertGreater(len(frames), 0)
        
        # We want to verify start and stop events were emitted
        starts = [f for f in frames if isinstance(f, UserStartedSpeakingFrame)]
        stops = [f for f in frames if isinstance(f, UserStoppedSpeakingFrame)]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(stops), 1)

if __name__ == "__main__":
    unittest.main()
