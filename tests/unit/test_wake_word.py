"""
Unit tests for WAKE_WORD_MODEL resolution (wake-word-default spec):
existing path → passthrough; bare name → bundled openwakeword model;
unknown → actionable WakeWordError; empty env → default applied.
"""

import importlib.util
import os

import pytest

from vocascade.edge.__main__ import (
    DEFAULT_WAKE_WORD,
    WakeWordError,
    _load_edge_config,
    resolve_wake_word_model,
)

OWW_INSTALLED = importlib.util.find_spec("openwakeword") is not None
needs_oww = pytest.mark.skipif(not OWW_INSTALLED, reason="openwakeword ([edge] extra) not installed")


def test_existing_path_used_as_is(tmp_path):
    custom = tmp_path / "my_word.onnx"
    custom.write_bytes(b"onnx")
    assert resolve_wake_word_model(str(custom)) == str(custom)


@needs_oww
def test_bundled_name_provisions_default():
    path = resolve_wake_word_model(DEFAULT_WAKE_WORD)
    assert path.endswith(".onnx")
    assert DEFAULT_WAKE_WORD in os.path.basename(path)
    assert os.path.exists(path)


@needs_oww
def test_unknown_name_fails_with_actionable_message():
    with pytest.raises(WakeWordError) as excinfo:
        resolve_wake_word_model("no_such_model_xyz")
    msg = str(excinfo.value)
    assert "no_such_model_xyz" in msg
    assert "WAKE_WORD_MODEL" in msg          # names the knob to fix
    assert DEFAULT_WAKE_WORD in msg          # lists the bundled alternatives


def test_empty_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("WAKE_WORD_MODEL", "")
    assert _load_edge_config()["wake_word_model"] == DEFAULT_WAKE_WORD


def test_receive_loop_plays_json_audio_frames():
    """Downstream audio arrives as JSON base64 (docs/protocol.md) — the edge
    must decode and play it, not just raw binary frames."""
    import asyncio
    import base64
    import json

    from vocascade.edge.__main__ import ClientState, SatelliteClient

    client = SatelliteClient({})
    client.state = ClientState.STREAMING

    pcm = b"\x01\x02" * 8
    frames = [
        json.dumps({"type": "status", "state": "assistant_streaming"}),
        json.dumps({"type": "audio", "data": base64.b64encode(pcm).decode(), "sample_rate": 32000}),
    ]

    class _WS:
        async def recv(self):
            if frames:
                return frames.pop(0)
            client.state = ClientState.LISTENING  # end the loop
            return json.dumps({"type": "audio_end"})

    class _Out:
        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

    client.ws = _WS()
    client.stream_out = _Out()
    asyncio.run(client._ws_receive_loop())
    assert client.stream_out.written == [pcm]
