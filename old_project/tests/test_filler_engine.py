#!/usr/bin/env python3
"""test_filler_engine.py — Standalone tests for FillerEngine."""

import tempfile
import struct
from pathlib import Path
from voice_satellite.filler_engine import FillerEngine


def make_pcm(n_samples: int = 100) -> bytes:
    """Create minimal valid 16-bit PCM bytes."""
    return struct.pack(f"<{n_samples}h", *([1000] * n_samples))


def test_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        engine = FillerEngine(Path(tmp))
        assert not engine.loaded()
        assert engine.get_filler("thinking") is None
        print("PASS: empty dir → loaded()=False, get_filler()=None")


def test_missing_dir():
    engine = FillerEngine(Path("/nonexistent/path/xyz"))
    assert not engine.loaded()
    print("PASS: missing dir → graceful degradation")


def test_load_single_filler():
    with tempfile.TemporaryDirectory() as tmp:
        cat_dir = Path(tmp) / "thinking"
        cat_dir.mkdir()
        (cat_dir / "hmm.pcm").write_bytes(make_pcm())

        engine = FillerEngine(Path(tmp))
        assert engine.loaded()
        assert engine.has_category("thinking")
        assert engine.category_count("thinking") == 1

        pcm = engine.get_filler("thinking")
        assert pcm is not None
        assert len(pcm) > 0
        print("PASS: load single filler, get_filler returns it")


def test_load_multiple_categories():
    with tempfile.TemporaryDirectory() as tmp:
        for cat in ["thinking", "working", "acknowledge"]:
            d = Path(tmp) / cat
            d.mkdir()
            (d / "a.pcm").write_bytes(make_pcm())
            (d / "b.pcm").write_bytes(make_pcm())

        engine = FillerEngine(Path(tmp))
        assert engine.loaded()
        for cat in ["thinking", "working", "acknowledge"]:
            assert engine.category_count(cat) == 2
        print("PASS: multiple categories loaded correctly")


def test_fallback_to_thinking():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "thinking"
        d.mkdir()
        (d / "hmm.pcm").write_bytes(make_pcm())

        engine = FillerEngine(Path(tmp))
        # Request empty category — should fall back to thinking
        pcm = engine.get_filler("working")
        assert pcm is not None
        print("PASS: fallback to 'thinking' when requested category is empty")


def test_odd_byte_trimmed():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "thinking"
        d.mkdir()
        # Write an odd number of bytes
        (d / "odd.pcm").write_bytes(b"\x01\x02\x03")

        engine = FillerEngine(Path(tmp))
        pcm = engine.get_filler("thinking")
        assert pcm is not None
        assert len(pcm) % 2 == 0, "PCM bytes must be even (16-bit samples)"
        print("PASS: odd-byte PCM trimmed to even length")


def test_empty_file_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "thinking"
        d.mkdir()
        (d / "empty.pcm").write_bytes(b"")
        (d / "valid.pcm").write_bytes(make_pcm())

        engine = FillerEngine(Path(tmp))
        assert engine.category_count("thinking") == 1  # only the valid one
        print("PASS: empty PCM file skipped")


def main():
    test_empty_dir()
    test_missing_dir()
    test_load_single_filler()
    test_load_multiple_categories()
    test_fallback_to_thinking()
    test_odd_byte_trimmed()
    test_empty_file_skipped()
    print("\nAll filler engine tests passed.")


if __name__ == "__main__":
    main()
