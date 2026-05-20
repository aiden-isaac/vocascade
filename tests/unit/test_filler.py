import tempfile
import unittest
from pathlib import Path
from voice_satellite.audio import FillerEngine

class TestFillerEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.filler_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_directory_graceful_handling(self):
        # Pass a non-existent path
        engine = FillerEngine(self.filler_dir / "does_not_exist")
        self.assertEqual(engine.get_categories(), {
            "thinking": 0,
            "working": 0,
            "acknowledge": 0,
            "slow_task": 0,
            "signoff": 0
        })
        self.assertIsNone(engine.get_filler("thinking"))

    def test_load_and_fallback(self):
        # Create category subdirectories
        thinking_dir = self.filler_dir / "thinking"
        working_dir = self.filler_dir / "working"
        thinking_dir.mkdir(parents=True)
        working_dir.mkdir(parents=True)

        # Write dummy PCM files
        (thinking_dir / "think1.pcm").write_bytes(b"think_audio_1")
        (thinking_dir / "think2.pcm").write_bytes(b"think_audio_2")
        (working_dir / "work1.pcm").write_bytes(b"work_audio_1")

        engine = FillerEngine(self.filler_dir)
        
        # Verify counts
        categories = engine.get_categories()
        self.assertEqual(categories["thinking"], 2)
        self.assertEqual(categories["working"], 1)
        self.assertEqual(categories["acknowledge"], 0)

        # Verify get_filler retrieves expected bytes
        filler_work = engine.get_filler("working")
        self.assertEqual(filler_work, b"work_audio_1")

        filler_think = engine.get_filler("thinking")
        self.assertIn(filler_think, [b"think_audio_1", b"think_audio_2"])

        # Verify fallback to thinking for empty categories
        filler_fallback = engine.get_filler("acknowledge")
        self.assertIn(filler_fallback, [b"think_audio_1", b"think_audio_2"])

        # Verify returning None for unsupported categories if thinking also empty
        # Clear thinking files
        (thinking_dir / "think1.pcm").unlink()
        (thinking_dir / "think2.pcm").unlink()
        
        engine_empty = FillerEngine(self.filler_dir)
        self.assertIsNone(engine_empty.get_filler("acknowledge"))

if __name__ == "__main__":
    unittest.main()
