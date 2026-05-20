import asyncio
import unittest
from voice_satellite.session import TaskTracker, TrackedTask

async def fake_coro_success(chunks=("hello ", "world")):
    for chunk in chunks:
        await asyncio.sleep(0)
        yield chunk

async def fake_coro_failure():
    await asyncio.sleep(0)
    raise RuntimeError("Simulated gateway failure")
    yield

class TestTaskTracker(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_and_complete(self):
        tracker = TaskTracker()
        completed = []

        async def on_complete(task: TrackedTask):
            completed.append(task)

        task_id = await tracker.spawn(
            gateway_coro=fake_coro_success(),
            agent_id="main",
            description="Test task",
            on_complete=on_complete,
        )
        self.assertTrue(task_id.startswith("task-"))
        self.assertEqual(len(tracker.all_running()), 1)

        await asyncio.sleep(0.1)
        self.assertEqual(len(tracker.all_running()), 0)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].status, "completed")
        self.assertEqual(completed[0].result, "hello world")

    async def test_spawn_failure(self):
        tracker = TaskTracker()
        completed = []

        async def on_complete(task: TrackedTask):
            completed.append(task)

        await tracker.spawn(
            gateway_coro=fake_coro_failure(),
            agent_id="main",
            description="Failing task",
            on_complete=on_complete,
        )

        await asyncio.sleep(0.1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].status, "failed")
        self.assertIn("Simulated", completed[0].error or "")

    async def test_get_summary_while_running(self):
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
        self.assertIn("running", summary)
        self.assertIn("Checking server health", summary)
        self.assertNotIn("ugin", summary)

        tracker.cancel_all()
        await asyncio.sleep(0.05)

    async def test_get_summary_empty(self):
        tracker = TaskTracker()
        summary = tracker.get_summary()
        self.assertEqual(summary, "")

    async def test_multiple_tasks(self):
        tracker = TaskTracker()
        completed = []

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

        self.assertEqual(len(tracker.all_running()), 3)
        await asyncio.sleep(0.1)
        self.assertEqual(len(tracker.all_running()), 0)
        self.assertEqual(sorted(completed), ["result_0", "result_1", "result_2"])

    async def test_cancel_all(self):
        tracker = TaskTracker()
        cancelled = []

        async def slow_coro():
            await asyncio.sleep(10)
            yield "never"

        async def on_complete(task: TrackedTask):
            if task.status == "cancelled":
                cancelled.append(True)

        await tracker.spawn(
            gateway_coro=slow_coro(),
            agent_id="main",
            description="Long task",
            on_complete=on_complete,
        )

        await asyncio.sleep(0.02)
        self.assertEqual(len(tracker.all_running()), 1)
        tracker.cancel_all()
        await asyncio.sleep(0.1)
        self.assertEqual(len(tracker.all_running()), 0)
        self.assertEqual(len(cancelled), 1)

    async def test_clear_completed(self):
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
        self.assertEqual(len(tracker.all_completed()), 1)

        tracker.clear_completed()
        self.assertEqual(len(tracker.all_completed()), 0)

    async def test_get_completed_pop(self):
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
        self.assertEqual(len(tracker.check_tasks()), 1)
        
        completed = tracker.get_completed()
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].result, "hello world")
        
        # Should be empty now
        self.assertEqual(len(tracker.check_tasks()), 0)

if __name__ == "__main__":
    unittest.main()
