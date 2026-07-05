"""Unit checks for the setup GUI's config-rewriting helpers (no server needed)."""

import os
from unittest.mock import patch

import httpx
import pytest

import vocascade.setup_server as setup_server
from vocascade.setup_server import (
    replace_block_list,
    set_scalar,
    require_sections,
    valid_fillers,
)

SAMPLE = """waterfall:
  stages:
    - stop
    - converse
    - hermes
  thresholds:
    high: 0.95   # gate
    medium: 0.65
"""


def test_reorder_preserves_other_lines_and_comments():
    out = replace_block_list(SAMPLE, "stages", ["hermes", "stop", "converse"])
    # new order applied
    assert out.index("- hermes") < out.index("- stop") < out.index("- converse")
    # the rest of the file is untouched, including the inline comment
    assert "high: 0.95   # gate" in out
    assert "thresholds:" in out


def test_reorder_requires_existing_key():
    with pytest.raises(ValueError):
        replace_block_list(SAMPLE, "nope", ["a"])


def test_set_scalar_updates_value_keeping_comment():
    out = set_scalar(SAMPLE, "high", 0.5)
    assert "high: 0.5   # gate" in out
    assert "medium: 0.65" in out  # unrelated scalar untouched


def test_require_sections_rejects_missing():
    require_sections({"system": {}, "waterfall": {}, "skills": {}})  # ok
    with pytest.raises(ValueError):
        require_sections({"system": {}, "waterfall": {}})  # no skills
    with pytest.raises(ValueError):
        require_sections(None)  # empty


def test_valid_fillers_shape():
    assert valid_fillers({"acknowledge": ["Yes?", "Go ahead."]})
    assert valid_fillers({})
    assert not valid_fillers({"bad": "not a list"})
    assert not valid_fillers({"bad": [1, 2]})
    assert not valid_fillers(["not", "a", "dict"])


# --- BYOK (D1/D7): GUI defaults and save→load_config round trip ---------------

def test_llm_group_first_with_empty_defaults():
    groups = list(setup_server.ENV_GROUPS)
    assert groups[0].startswith("LLM")
    llm_fields = {k: d for k, d, _ in setup_server.ENV_GROUPS[groups[0]]}
    assert llm_fields["LLM_BASE_URL"] == ""
    assert llm_fields["LLM_MODEL"] == ""


def test_no_personal_defaults_anywhere():
    all_defaults = " ".join(
        d for grp in setup_server.ENV_GROUPS.values() for _, d, _ in grp
    )
    assert "frizzt" not in all_defaults
    assert "8642" not in all_defaults


def test_gui_saved_values_pass_load_config(tmp_path):
    """Values written via POST /api/env are sufficient for load_config (D7)."""
    from fastapi.testclient import TestClient
    from dotenv import dotenv_values
    from vocascade.config import load_config

    env_file = tmp_path / ".env"
    with patch.object(setup_server, "ENV_PATH", env_file):
        client = TestClient(setup_server.app)
        resp = client.post("/api/env", json={"values": {
            "LLM_BASE_URL": "http://localhost:11434/v1",
            "LLM_MODEL": "llama3.2",
            "LLM_API_KEY": "",
        }})
        assert resp.status_code == 200

    saved = {k: v or "" for k, v in dotenv_values(env_file).items()}
    with patch.dict(os.environ, saved, clear=True):
        with patch("vocascade.config.load_dotenv"):
            config = load_config()  # no ValueError: LLM validation passes
    assert config.llm_base_url == "http://localhost:11434/v1"
    assert config.llm_model == "llama3.2"


# --- test-connection endpoints (D7) -------------------------------------------

def _llm_transport(handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    return patch("vocascade.gateway.local_llm.httpx.AsyncClient",
                 lambda *a, **kw: real_client(transport=transport))


@pytest.mark.parametrize("status,verdict", [(200, "ok"), (401, "auth"), (500, "unreachable")])
def test_test_llm_verdicts(status, verdict):
    from fastapi.testclient import TestClient

    body = {"choices": [{"message": {"content": "pong"}}]} if status == 200 else None
    with _llm_transport(lambda req: httpx.Response(status, json=body)):
        client = TestClient(setup_server.app)
        resp = client.post("/api/test-llm", json={
            "base_url": "http://test/v1", "model": "m", "api_key": "k"})
    assert resp.status_code == 200
    assert resp.json()["verdict"] == verdict


def test_test_llm_requires_base_url_and_model():
    from fastapi.testclient import TestClient
    client = TestClient(setup_server.app)
    assert client.post("/api/test-llm", json={"base_url": ""}).status_code == 400
