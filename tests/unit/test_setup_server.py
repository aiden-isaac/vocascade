"""Unit checks for the setup GUI's config-rewriting helpers (no server needed)."""

import pytest

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
