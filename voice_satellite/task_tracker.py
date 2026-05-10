"""
voice_satellite/task_tracker.py — Async OpenClaw background task manager.

When the LLM router decides to call OpenClaw for a complex task, server.py
can optionally run the gateway call in the background (non-blocking) and
register a completion callback. The callback wakes the session proactively
when the agent's final message arrives.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class TrackedTask:
    task_id: str
    agent_id: str
    description: str
    status: Literal["running", "completed", "failed"] = "running"
    result: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    asyncio_task: asyncio.Task | None = field(default=None, repr=False)

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def elapsed_str(self) -> str:
        secs = int(self.elapsed_seconds())
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m{secs % 60}s"


# Signature: async (task: TrackedTask) -> None
OnCompleteCallback = Callable[["TrackedTask"], Awaitable[None]]


class TaskTracker:
    """
    Manages background OpenClaw agent tasks. Each task runs concurrently and
    fires an on_complete callback (registered per-task) when done.

    Usage in server.py:
        tracker = TaskTracker()
        task_id = await tracker.spawn(
            gateway_coro=gateway.stream_one_shot(agent_id, message),
            agent_id=agent_id,
            description="Researching X...",
            on_complete=handle_task_complete,
        )
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TrackedTask] = {}

    async def spawn(
        self,
        gateway_coro: "AsyncIterator[str]",  # type: ignore[name-defined]
        agent_id: str,
        description: str,
        on_complete: OnCompleteCallback,
    ) -> str:
        """
        Spawn a background task that collects all chunks from `gateway_coro`
        and calls `on_complete` when done (or on failure).

        Returns the task_id.
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        tracked = TrackedTask(
            task_id=task_id,
            agent_id=agent_id,
            description=description,
        )
        self._tasks[task_id] = tracked

        async def _run() -> None:
            chunks: list[str] = []
            try:
                async for chunk in gateway_coro:
                    if chunk:
                        chunks.append(chunk)
                tracked.result = "".join(chunks)
                tracked.status = "completed"
                logger.info(
                    "TaskTracker: task %s completed in %s (%d chars)",
                    task_id,
                    tracked.elapsed_str(),
                    len(tracked.result),
                )
            except asyncio.CancelledError:
                tracked.status = "failed"
                tracked.error = "cancelled"
                logger.info("TaskTracker: task %s was cancelled", task_id)
                raise
            except Exception as exc:
                tracked.status = "failed"
                tracked.error = str(exc)
                logger.error("TaskTracker: task %s failed: %s", task_id, exc)

            try:
                await on_complete(tracked)
            except Exception as exc:
                logger.error(
                    "TaskTracker: on_complete callback for %s raised: %s", task_id, exc
                )

        asyncio_task = asyncio.create_task(_run())
        tracked.asyncio_task = asyncio_task
        logger.info(
            "TaskTracker: spawned task %s (agent=%s, desc=%r)",
            task_id,
            agent_id,
            description[:60],
        )
        return task_id

    def get_task(self, task_id: str) -> TrackedTask | None:
        return self._tasks.get(task_id)

    def all_running(self) -> list[TrackedTask]:
        return [t for t in self._tasks.values() if t.status == "running"]

    def all_completed(self) -> list[TrackedTask]:
        return [t for t in self._tasks.values() if t.status == "completed"]

    def get_summary(self) -> str:
        """
        Returns a short natural-language status string for injection into the
        LLM system prompt, e.g.:
          "2 background tasks running: [task-abc1: Researching X (12s elapsed)]"
        """
        running = self.all_running()
        if not running:
            return ""

        parts = [
            f"[{t.task_id}: {t.description} ({t.elapsed_str()} elapsed)]"
            for t in running
        ]
        count = len(running)
        label = "task" if count == 1 else "tasks"
        return f"{count} background {label} running: " + ", ".join(parts)

    def cancel_all(self) -> None:
        """Cancel all running tasks (called on session close)."""
        for task in self._tasks.values():
            if task.status == "running" and task.asyncio_task and not task.asyncio_task.done():
                task.asyncio_task.cancel()

    def clear_completed(self) -> None:
        """Purge completed/failed tasks to prevent unbounded growth."""
        done_ids = [
            tid for tid, t in self._tasks.items() if t.status != "running"
        ]
        for tid in done_ids:
            del self._tasks[tid]
