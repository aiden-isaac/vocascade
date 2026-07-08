import unittest
from vocascade.pipeline.pipeline import (
    VoicePipeline,
    PipelineStage,
    TextFrame,
    AudioFrame,
    InterruptionFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame
)
from vocascade.pipeline.tts import TTSStage
from vocascade.tts.protocol import TTSBackend

class MockStage(PipelineStage):
    def __init__(self):
        super().__init__()
        self.received_frames = []

    async def push(self, frame):
        self.received_frames.append(frame)

class FakeTTSClient:
    """Minimal TTSBackend-conforming fake: the stage must work with any
    protocol-conforming backend, not just Genie."""

    def __init__(self, chunks=(b"\x01\x02", b"\x03\x04")):
        self.sample_rate = 32000
        self.degraded_mode = False
        self.chunks = chunks
        self.start_calls = 0
        self.stop_calls = 0
        self.close_calls = 0
        self.synthesize_calls = []

    async def start(self):
        self.start_calls += 1

    async def synthesize(self, text, *, session=""):
        self.synthesize_calls.append(text)
        for chunk in self.chunks:
            yield chunk

    async def stop(self):
        self.stop_calls += 1

    async def close(self):
        self.close_calls += 1

class TestTTSStage(unittest.IsolatedAsyncioTestCase):
    async def test_fake_conforms_to_protocol(self):
        self.assertIsInstance(FakeTTSClient(), TTSBackend)

    async def test_start_preloads_voice(self):
        client = FakeTTSClient()
        stage = TTSStage(client, voice_name="test-character")

        await stage.start()
        self.assertEqual(client.start_calls, 1)
        self.assertTrue(stage._voice_loaded)

    async def test_synthesis_success(self):
        client = FakeTTSClient()
        stage = TTSStage(client, voice_name="test-character")

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

    async def test_synthesis_sentinel_strip(self):
        client = FakeTTSClient()
        stage = TTSStage(client, voice_name="test-character")

        next_stage = MockStage()
        stage.next_stage = next_stage

        pipeline = VoicePipeline([stage, next_stage])
        await pipeline.start()

        # Text only containing the sentinel should be stripped and not synthesize
        await stage.push(TextFrame(text="end session"))
        await pipeline.stop()

        self.assertEqual(len(next_stage.received_frames), 0)
        self.assertEqual(client.synthesize_calls, [])

    async def test_synthesis_interrupted_by_event(self):
        client = FakeTTSClient()
        stage = TTSStage(client, voice_name="test-character")
        next_stage = MockStage()
        stage.next_stage = next_stage
        pipeline = VoicePipeline([stage, next_stage])

        # Generator that triggers pipeline interrupt on first yield
        async def interrupting_synthesize(text, *, session=""):
            yield b"\x01\x02"
            pipeline.interrupt_event.set()
            yield b"\x03\x04"

        client.synthesize = interrupting_synthesize

        await pipeline.start()
        await stage.push(TextFrame(text="Hello world"))
        await pipeline.stop()

        # Should only receive first AudioFrame before interrupt is checked
        frames = next_stage.received_frames
        self.assertGreaterEqual(len(frames), 2)
        self.assertIsInstance(frames[0], BotStartedSpeakingFrame)
        self.assertIsInstance(frames[1], AudioFrame)
        # Should call client.stop() on interrupt
        self.assertGreater(client.stop_calls, 0)
        # Should also ensure BotStoppedSpeakingFrame is sent in finally block
        self.assertIsInstance(frames[-1], BotStoppedSpeakingFrame)

    async def test_resamples_native_rate_to_wire_rate(self):
        # 22050 Hz backend (Piper medium voices) into a 32000 Hz wire format.
        src = (b"\x00\x10" * 2205)  # 100ms of constant s16le samples @ 22050
        client = FakeTTSClient(chunks=(src,))
        client.sample_rate = 22050
        stage = TTSStage(client, out_sample_rate=32000, voice_name="test-character")

        next_stage = MockStage()
        stage.next_stage = next_stage
        pipeline = VoicePipeline([stage, next_stage])
        await pipeline.start()
        await stage.push(TextFrame(text="Hello world"))
        await pipeline.stop()

        audio_frames = [f for f in next_stage.received_frames if isinstance(f, AudioFrame)]
        self.assertEqual(len(audio_frames), 1)
        frame = audio_frames[0]
        self.assertEqual(frame.sample_rate, 32000)
        # 100ms at 32000 Hz mono s16le = 3200 samples = 6400 bytes.
        self.assertEqual(len(frame.audio), 6400)
        self.assertEqual(frame.audio[:2], b"\x00\x10")  # constant signal survives

    async def test_interruption_frame(self):
        client = FakeTTSClient()
        stage = TTSStage(client, voice_name="test-character")
        next_stage = MockStage()
        stage.next_stage = next_stage

        await stage.push(InterruptionFrame())

        self.assertEqual(client.stop_calls, 1)
        self.assertEqual(len(next_stage.received_frames), 1)
        self.assertIsInstance(next_stage.received_frames[0], InterruptionFrame)

if __name__ == "__main__":
    unittest.main()
