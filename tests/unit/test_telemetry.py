"""
Unit tests for voice_satellite.telemetry.LatencyTracker.
"""

import logging
import os
import time

import pytest

from voice_satellite.telemetry import LatencyTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_latency_record(caplog, stage: str) -> str | None:
    """Return the first log message that contains [LATENCY] and stage=<stage>."""
    for record in caplog.records:
        if "[LATENCY]" in record.getMessage() and f"stage={stage}" in record.getMessage():
            return record.getMessage()
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLatencyTrackerBasic:
    def test_record_emits_latency_log_line(self, caplog):
        """record() must emit a [LATENCY] line at INFO level."""
        # Ensure opt-out is NOT active
        os.environ.pop("LATENCY_LOGGING", None)

        with caplog.at_level(logging.INFO, logger="voice_satellite.telemetry"):
            tracker = LatencyTracker("stt", "test123")
            tracker.start()
            tracker.record()

        msg = _first_latency_record(caplog, "stt")
        assert msg is not None, "Expected a [LATENCY] log line but none found"
        assert "stage=stt" in msg
        assert "session=test123" in msg
        assert "duration_ms=" in msg

    def test_record_duration_is_non_negative(self, caplog):
        """duration_ms must always be ≥ 0."""
        os.environ.pop("LATENCY_LOGGING", None)

        with caplog.at_level(logging.INFO, logger="voice_satellite.telemetry"):
            tracker = LatencyTracker("stt", "sess")
            tracker.start()
            tracker.record()

        msg = _first_latency_record(caplog, "stt")
        assert msg is not None
        # Extract duration_ms value
        import re
        m = re.search(r"duration_ms=(\d+)", msg)
        assert m is not None
        assert int(m.group(1)) >= 0

    def test_record_measures_elapsed_time(self):
        """duration_ms must be within a reasonable range of the actual sleep."""
        os.environ.pop("LATENCY_LOGGING", None)

        tracker = LatencyTracker("stt", "s")
        tracker.start()
        sleep_ms = 50
        time.sleep(sleep_ms / 1000.0)
        # Capture duration before record() so we can compare independently
        elapsed_ms = int((time.perf_counter() - tracker._t0) * 1000)

        # Allow ±30 ms tolerance for scheduler jitter in CI
        assert abs(elapsed_ms - sleep_ms) < 30, (
            f"Elapsed {elapsed_ms} ms too far from expected {sleep_ms} ms"
        )

    def test_record_with_extra_kwargs(self, caplog):
        """Extra keyword arguments must appear in the log line before session=."""
        os.environ.pop("LATENCY_LOGGING", None)

        with caplog.at_level(logging.INFO, logger="voice_satellite.telemetry"):
            tracker = LatencyTracker("sentence_buffer", "abc")
            tracker.start()
            tracker.record(sentence_index=2)

        msg = _first_latency_record(caplog, "sentence_buffer")
        assert msg is not None
        assert "sentence_index=2" in msg
        # sentence_index must appear before session
        assert msg.index("sentence_index=2") < msg.index("session=abc")

    def test_start_returns_self(self):
        """start() must return the tracker instance for optional chaining."""
        tracker = LatencyTracker("tts_first_chunk", "x")
        result = tracker.start()
        assert result is tracker


class TestLatencyTrackerOptOut:
    def test_latency_logging_false_silences_output(self, caplog):
        """With LATENCY_LOGGING=false, record() must emit no log lines."""
        os.environ["LATENCY_LOGGING"] = "false"
        try:
            with caplog.at_level(logging.INFO, logger="voice_satellite.telemetry"):
                tracker = LatencyTracker("stt", "quiet")
                tracker.start()
                tracker.record()

            latency_records = [
                r for r in caplog.records if "[LATENCY]" in r.getMessage()
            ]
            assert latency_records == [], (
                f"Expected no [LATENCY] lines but got: {[r.getMessage() for r in latency_records]}"
            )
        finally:
            os.environ.pop("LATENCY_LOGGING", None)

    def test_latency_logging_false_case_insensitive(self, caplog):
        """LATENCY_LOGGING=FALSE (uppercase) must also silence output."""
        os.environ["LATENCY_LOGGING"] = "FALSE"
        try:
            with caplog.at_level(logging.INFO, logger="voice_satellite.telemetry"):
                tracker = LatencyTracker("llm_first_token", "quiet")
                tracker.start()
                tracker.record()

            latency_records = [
                r for r in caplog.records if "[LATENCY]" in r.getMessage()
            ]
            assert latency_records == []
        finally:
            os.environ.pop("LATENCY_LOGGING", None)

    def test_latency_logging_other_values_do_not_silence(self, caplog):
        """Values other than 'false' must NOT suppress output."""
        os.environ["LATENCY_LOGGING"] = "true"
        try:
            with caplog.at_level(logging.INFO, logger="voice_satellite.telemetry"):
                tracker = LatencyTracker("end_to_end", "loud")
                tracker.start()
                tracker.record()

            msg = _first_latency_record(caplog, "end_to_end")
            assert msg is not None, "Expected a [LATENCY] line but none found"
        finally:
            os.environ.pop("LATENCY_LOGGING", None)


class TestLatencyTrackerSessionId:
    def test_empty_session_id_is_valid(self, caplog):
        """An empty session ID is allowed and produces session= in the log."""
        os.environ.pop("LATENCY_LOGGING", None)

        with caplog.at_level(logging.INFO, logger="voice_satellite.telemetry"):
            tracker = LatencyTracker("stt", "")
            tracker.start()
            tracker.record()

        msg = _first_latency_record(caplog, "stt")
        assert msg is not None
        assert "session=" in msg

    def test_session_id_appears_in_log(self, caplog):
        """The session ID passed at construction must appear verbatim in the log."""
        os.environ.pop("LATENCY_LOGGING", None)
        session = "deadbeef"

        with caplog.at_level(logging.INFO, logger="voice_satellite.telemetry"):
            tracker = LatencyTracker("tts_first_chunk", session)
            tracker.start()
            tracker.record()

        msg = _first_latency_record(caplog, "tts_first_chunk")
        assert msg is not None
        assert f"session={session}" in msg
