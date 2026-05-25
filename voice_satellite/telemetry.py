"""
Latency instrumentation helper for the Voice Satellite pipeline.

Provides a lightweight LatencyTracker that wraps time.perf_counter() and
emits structured [LATENCY] log lines at INFO level. All instrumentation is
additive — no pipeline logic is altered.

Opt-out: set LATENCY_LOGGING=false (case-insensitive) in the environment
to silence all [LATENCY] output without requiring a restart.
"""

import logging
import os
import time

logger = logging.getLogger("voice_satellite.telemetry")


class LatencyTracker:
    """
    Thin wrapper around time.perf_counter() that logs a structured [LATENCY]
    line when record() is called.

    Usage::

        tracker = LatencyTracker("stt", session_id)
        tracker.start()
        result = await do_expensive_work()
        tracker.record()          # → [LATENCY] stage=stt duration_ms=412 session=abc123

    Extra key=value pairs can be attached per-record::

        tracker.record(sentence_index=0)
        # → [LATENCY] stage=sentence_buffer duration_ms=87 sentence_index=0 session=abc123
    """

    __slots__ = ("_stage", "_session", "_t0")

    def __init__(self, stage: str, session: str = "") -> None:
        self._stage = stage
        self._session = session
        self._t0: float = 0.0

    def start(self) -> "LatencyTracker":
        """Record the start timestamp. Returns self for optional chaining."""
        self._t0 = time.perf_counter()
        return self

    def record(self, **extra) -> None:
        """
        Compute elapsed time since start() and emit a [LATENCY] log line.

        If the environment variable LATENCY_LOGGING is set to 'false'
        (case-insensitive), this method is a no-op.
        """
        if os.environ.get("LATENCY_LOGGING", "").strip().lower() == "false":
            return

        duration_ms = int((time.perf_counter() - self._t0) * 1000)

        # Build extra key=value pairs (inserted between duration_ms and session)
        extra_str = ""
        if extra:
            extra_str = " " + " ".join(f"{k}={v}" for k, v in extra.items())

        logger.info(
            "[LATENCY] stage=%s duration_ms=%d%s session=%s",
            self._stage,
            duration_ms,
            extra_str,
            self._session,
        )
