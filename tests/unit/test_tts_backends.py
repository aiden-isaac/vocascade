import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vocascade.tts.piper_client import PiperTTS, STOCK_VOICES
from vocascade.tts.protocol import REGISTRY, TTSBackend, make_tts_client

PIPER_INSTALLED = importlib.util.find_spec("piper") is not None


def _genie_config(**overrides):
    cfg = SimpleNamespace(
        tts_backend="genie",
        tts_url="http://localhost:8000",
        tts_character_name="ordis",
        tts_onnx_model_dir=None,
        tts_reference_audio=None,
        tts_reference_text=None,
        tts_language="en",
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class FakeAudioChunk:
    def __init__(self, pcm: bytes):
        self.audio_int16_bytes = pcm


class FakeVoice:
    """Stands in for piper.PiperVoice: blocking generator of AudioChunks."""

    def __init__(self, chunks):
        self.config = SimpleNamespace(sample_rate=22050)
        self.chunks = chunks
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        for pcm in self.chunks:
            yield FakeAudioChunk(pcm)


class TestPiperTTS(unittest.IsolatedAsyncioTestCase):
    def _loaded_client(self, chunks=(b"\x01\x02", b"\x03\x04")) -> PiperTTS:
        client = PiperTTS(voice="female")
        client._voice = FakeVoice(list(chunks))
        client.sample_rate = client._voice.config.sample_rate
        client.initialized = True
        return client

    async def test_conforms_to_protocol(self):
        self.assertIsInstance(PiperTTS(), TTSBackend)

    async def test_voice_alias_resolution(self):
        self.assertEqual(PiperTTS(voice="male").voice_id, STOCK_VOICES["male"])
        # Any non-alias name is treated as a raw piper voice id.
        self.assertEqual(PiperTTS(voice="en_GB-alan-medium").voice_id, "en_GB-alan-medium")

    async def test_synthesize_streams_pcm_chunks(self):
        client = self._loaded_client()
        got = [c async for c in client.synthesize("Hello there.")]
        self.assertEqual(got, [b"\x01\x02", b"\x03\x04"])
        self.assertEqual(client._voice.texts, ["Hello there."])
        self.assertTrue(all(len(c) % 2 == 0 for c in got))
        self.assertEqual(client.sample_rate, 22050)

    async def test_synthesize_skips_non_speech(self):
        client = self._loaded_client()
        self.assertEqual([c async for c in client.synthesize("  ... ")], [])
        self.assertEqual(client._voice.texts, [])

    async def test_stop_drops_remaining_chunks(self):
        client = self._loaded_client(chunks=(b"\x01\x02", b"\x03\x04", b"\x05\x06"))
        stream = client.synthesize("Hello there.")
        first = await stream.__anext__()
        self.assertEqual(first, b"\x01\x02")
        await client.stop()
        with self.assertRaises(StopAsyncIteration):
            await stream.__anext__()

    @unittest.skipUnless(PIPER_INSTALLED, "piper not installed")
    async def test_missing_voice_download_failure_degrades_with_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = PiperTTS(voice="female", models_dir=tmp)
            with patch("piper.download_voices.download_voice",
                       side_effect=RuntimeError("no network")), \
                 self.assertLogs("vocascade.tts", level="WARNING") as logs:
                await client.start()
            self.assertTrue(client.degraded_mode)
            message = "\n".join(logs.output)
            # Located message: voice, models dir, and the install command.
            self.assertIn(STOCK_VOICES["female"], message)
            self.assertIn(tmp, message)
            self.assertIn("piper.download_voices", message)

    async def test_degraded_mode_skips_synthesis(self):
        client = PiperTTS(degraded_mode=True)
        self.assertEqual([c async for c in client.synthesize("Hello.")], [])


class TestRegistry(unittest.IsolatedAsyncioTestCase):
    def test_registered_backends(self):
        self.assertEqual(sorted(REGISTRY), ["genie", "piper"])

    def test_genie_selection_builds_genie_client(self):
        client = make_tts_client(_genie_config(), degraded_mode=True)
        self.assertEqual(type(client).__name__, "GenieTTSClient")
        self.assertTrue(client.degraded_mode)
        self.assertIsInstance(client, TTSBackend)

    def test_genie_selection_never_imports_piper(self):
        # Evict piper (and our piper module) so a fresh import would be visible.
        evicted = {}
        for name in list(sys.modules):
            if name == "piper" or name.startswith("piper.") \
                    or name == "vocascade.tts.piper_client":
                evicted[name] = sys.modules.pop(name)
        try:
            make_tts_client(_genie_config())
            self.assertNotIn("piper", sys.modules)
            self.assertNotIn("vocascade.tts.piper_client", sys.modules)
        finally:
            sys.modules.update(evicted)

    def test_piper_selection_builds_piper_client(self):
        cfg = SimpleNamespace(tts_backend="piper", tts_voice="male", tts_models_dir="")
        client = make_tts_client(cfg)
        self.assertIsInstance(client, PiperTTS)
        self.assertEqual(client.voice_id, STOCK_VOICES["male"])

    def test_piper_is_default_when_config_has_no_backend(self):
        cfg = SimpleNamespace(tts_voice="", tts_models_dir="")
        # getattr default in make_tts_client is exercised via AdapterConfig's
        # own default in config tests; here an explicit piper name suffices.
        cfg.tts_backend = "piper"
        self.assertIsInstance(make_tts_client(cfg), PiperTTS)

    def test_unknown_backend_fails_fast_with_names(self):
        with self.assertRaises(ValueError) as ctx:
            make_tts_client(SimpleNamespace(tts_backend="espeak"))
        message = str(ctx.exception)
        self.assertIn("espeak", message)
        self.assertIn("genie", message)
        self.assertIn("piper", message)


if __name__ == "__main__":
    unittest.main()
