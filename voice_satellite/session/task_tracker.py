"""
Async background task tracker and manager.
"""

import asyncio
import logging
import time
import uuid
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger("voice_satellite.session")

@dataclass
class TrackedTask:
    task_id: str
    agent_id: str
    description: str
    status: str = "running"  # "running", "completed", "failed", "cancelled"
    result: Any = None
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

class TaskTracker:
    """
    Manages background tasks (e.g. OpenClaw requests), tracking their statuses,
    handling completions with callbacks, and allowing cancellation/polling.
    """
    def __init__(self) -> None:
        self._tasks: dict[str, TrackedTask] = {}
        self._callbacks: list[Callable[[TrackedTask], Awaitable[None] | None]] = []

    def register_callback(self, callback: Callable[[TrackedTask], Awaitable[None] | None]) -> None:
        """
        Registers a tracker-wide callback called when any background task completes.
        """
        self._callbacks.append(callback)

    def start_task(
        self,
        agent_id: str,
        description: str,
        coro: Any,
        on_complete: Callable[[TrackedTask], Awaitable[None] | None] | None = None
    ) -> str:
        """
        Wraps a coroutine or async generator in an asyncio.Task and starts tracking it.
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        tracked = TrackedTask(
            task_id=task_id,
            agent_id=agent_id,
            description=description,
        )
        self._tasks[task_id] = tracked

        async def _run() -> None:
            try:
                # Support both async generators (streaming) and standard coroutines
                if inspect.isasyncgen(coro) or (hasattr(coro, "__aiter__") and not inspect.iscoroutine(coro)):
                    chunks = []
                    async for chunk in coro:
                        if chunk:
                            chunks.append(chunk)
                    tracked.result = "".join(chunks)
                else:
                    tracked.result = await coro
                
                tracked.status = "completed"
                logger.info(f"TaskTracker: task {task_id} completed successfully")
            except asyncio.CancelledError:
                tracked.status = "cancelled"
                tracked.error = "cancelled"
                logger.info(f"TaskTracker: task {task_id} was cancelled")
                raise
            except Exception as e:
                tracked.status = "failed"
                tracked.error = str(e)
                logger.error(f"TaskTracker: task {task_id} failed: {e}")
            finally:
                # Execute task-specific callback
                if on_complete:
                    try:
                        res = on_complete(tracked)
                        if inspect.isawaitable(res):
                            await res
                    except Exception as e:
                        logger.error(f"TaskTracker: on_complete callback for {task_id} failed: {e}")
                
                # Execute global tracker callbacks
                for cb in self._callbacks:
                    try:
                        res = cb(tracked)
                        if inspect.isawaitable(res):
                            await res
                    except Exception as e:
                        logger.error(f"TaskTracker: global callback failed on task {task_id}: {e}")

        asyncio_task = asyncio.create_task(_run())
        tracked.asyncio_task = asyncio_task
        logger.info(f"TaskTracker: spawned background task {task_id} for agent {agent_id}")
        return task_id

    # Compatibility alias for old tests
    async def spawn(
        self,
        gateway_coro: Any,
        agent_id: str,
        description: str,
        on_complete: Any
    ) -> str:
        return self.start_task(agent_id, description, gateway_coro, on_complete)

    def check_tasks(self) -> list[TrackedTask]:
        """
        Returns the statuses of all tracked tasks.
        """
        return list(self._tasks.values())

    def get_completed(self) -> list[TrackedTask]:
        """
        Pops and returns completed tasks since last check.
        """
        completed = [t for t in self._tasks.values() if t.status in {"completed", "failed", "cancelled"}]
        for t in completed:
            self._tasks.pop(t.task_id, None)
        return completed

    def all_running(self) -> list[TrackedTask]:
        """
        Returns all running tasks.
        """
        return [t for t in self._tasks.values() if t.status == "running"]

    def all_completed(self) -> list[TrackedTask]:
        """
        Returns all non-running tasks.
        """
        return [t for t in self._tasks.values() if t.status in {"completed", "failed", "cancelled"}]

    def get_summary(self) -> str:
        """
        Generates a natural-language summary of running tasks for prompt context.
        """
        running = self.all_running()
        if not running:
            return ""
        parts = [f"[{t.task_id}: {t.description} ({t.elapsed_str()} elapsed)]" for t in running]
        count = len(running)
        label = "task" if count == 1 else "tasks"
        return f"{count} background {label} running: " + ", ".join(parts)

    def cancel_all(self) -> None:
        """
        Cancels all running tasks.
        """
        for t in self._tasks.values():
            if t.status == "running" and t.asyncio_task and not t.asyncio_task.done():
                t.asyncio_task.cancel()

    def clear_completed(self) -> None:
        """
        Purges completed tasks to prevent unbound growth.
        """
        done_ids = [tid for tid, t in self._tasks.items() if t.status != "running"]
        for tid in done_ids:
            self._tasks.pop(tid, None)
