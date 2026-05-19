#!/usr/bin/env python3
"""test_task_tracker.py — Standalone tests for TaskTracker."""

import asyncio
from voice_satellite.task_tracker import TaskTracker, TrackedTask


async def fake_coro_success(chunks=("hello ", "world")):
    """Async generator that yields chunks then returns."""
    for chunk in chunks:
        await asyncio.sleep(0)
        yield chunk


async def fake_coro_failure():
    await asyncio.sleep(0)
    raise RuntimeError("Simulated gateway failure")
    yield  # make it an async generator


async def test_spawn_and_complete():
    tracker = TaskTracker()
    completed: list[TrackedTask] = []

    async def on_complete(task: TrackedTask):
        completed.append(task)

    task_id = await tracker.spawn(
        gateway_coro=fake_coro_success(),
        agent_id="main",
        description="Test task",
        on_complete=on_complete,
    )
    assert task_id.startswith("task-")
    assert len(tracker.all_running()) == 1

    # Wait for it to complete
    await asyncio.sleep(0.1)
    assert len(tracker.all_running()) == 0
    assert len(completed) == 1
    assert completed[0].status == "completed"
    assert completed[0].result == "hello world"
    print("PASS: task spawns, completes, fires callback")


async def test_spawn_failure():
    tracker = TaskTracker()
    completed: list[TrackedTask] = []

    async def on_complete(task: TrackedTask):
        completed.append(task)

    await tracker.spawn(
        gateway_coro=fake_coro_failure(),
        agent_id="main",
        description="Failing task",
        on_complete=on_complete,
    )

    await asyncio.sleep(0.1)
    assert len(completed) == 1
    assert completed[0].status == "failed"
    assert "Simulated" in (completed[0].error or "")
    print("PASS: failed task fires callback with status=failed")


async def test_get_summary_while_running():
    tracker = TaskTracker()

    async def slow_coro():
        await asyncio.sleep(0.5)
        yield "result"

    async def noop(task):
        pass

    await tracker.spawn(
        gateway_coro=slow_coro(),
        agent_id="ugin",
        description="Checking server health",
        on_complete=noop,
    )

    summary = tracker.get_summary()
    assert "running" in summary
    assert "Checking server health" in summary
    assert "ugin" not in summary  # description is shown, not agent_id
    print(f"PASS: get_summary while running: {summary!r}")

    # Clean up
    tracker.cancel_all()
    await asyncio.sleep(0.05)


async def test_get_summary_empty():
    tracker = TaskTracker()
    summary = tracker.get_summary()
    assert summary == ""
    print("PASS: get_summary with no tasks returns empty string")


async def test_multiple_tasks():
    tracker = TaskTracker()
    completed: list[str] = []

    async def make_coro(val):
        await asyncio.sleep(0)
        yield val

    async def on_complete(task: TrackedTask):
        completed.append(task.result or "")

    for i in range(3):
        await tracker.spawn(
            gateway_coro=make_coro(f"result_{i}"),
            agent_id="main",
            description=f"Task {i}",
            on_complete=on_complete,
        )

    assert len(tracker.all_running()) == 3
    await asyncio.sleep(0.1)
    assert len(tracker.all_running()) == 0
    assert sorted(completed) == ["result_0", "result_1", "result_2"]
    print("PASS: 3 concurrent tasks all complete correctly")


async def test_cancel_all():
    tracker = TaskTracker()
    cancelled: list[bool] = []

    async def slow_coro():
        await asyncio.sleep(10)
        yield "never"

    async def on_complete(task: TrackedTask):
        if task.status == "failed":
            cancelled.append(True)

    await tracker.spawn(
        gateway_coro=slow_coro(),
        agent_id="main",
        description="Long task",
        on_complete=on_complete,
    )

    await asyncio.sleep(0.02)
    assert len(tracker.all_running()) == 1
    tracker.cancel_all()
    await asyncio.sleep(0.1)
    assert len(tracker.all_running()) == 0
    print("PASS: cancel_all() cancels running tasks")


async def test_clear_completed():
    tracker = TaskTracker()

    async def noop(task):
        pass

    await tracker.spawn(
        gateway_coro=fake_coro_success(),
        agent_id="main",
        description="Quick task",
        on_complete=noop,
    )
    await asyncio.sleep(0.1)
    assert len(tracker.all_completed()) == 1

    tracker.clear_completed()
    assert len(tracker.all_completed()) == 0
    print("PASS: clear_completed() removes finished tasks")


async def main():
    await test_spawn_and_complete()
    await test_spawn_failure()
    await test_get_summary_while_running()
    await test_get_summary_empty()
    await test_multiple_tasks()
    await test_cancel_all()
    await test_clear_completed()
    print("\nAll task tracker tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
