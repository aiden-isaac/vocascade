import unittest
import time
from vocascade.transcript_manager import (
    TranscriptManager,
    TranscriptTurn,
    HermesTaskState,
    HermesTask
)

class TestTranscriptManager(unittest.TestCase):
    def setUp(self):
        self.tm = TranscriptManager(max_turns=5)

    def test_append_and_sliding_window(self):
        # Append 7 turns, sliding window is max 5
        for i in range(7):
            self.tm.append(TranscriptTurn(role="user", content=f"Turn {i}"))
        
        window = self.tm.get_window()
        self.assertEqual(len(window), 5)
        # Should contain the last 5 turns: Turn 2 to Turn 6
        self.assertEqual(window[0].content, "Turn 2")
        self.assertEqual(window[-1].content, "Turn 6")

    def test_state_updates(self):
        # Append turn with task ID
        task_id = "task_20260603_120000_01"
        self.tm.append(TranscriptTurn(
            role="user",
            content="Deploy service",
            hermes_task_id=task_id,
            hermes_state=HermesTaskState.PENDING
        ))
        
        # Verify it is tracked in tasks
        self.assertIn(task_id, self.tm.tasks)
        self.assertEqual(self.tm.tasks[task_id].state, HermesTaskState.PENDING)
        
        # Update state
        self.tm.update_state(task_id, HermesTaskState.EXECUTING)
        self.assertEqual(self.tm.tasks[task_id].state, HermesTaskState.EXECUTING)
        self.assertEqual(self.tm.get_window()[0].hermes_state, HermesTaskState.EXECUTING)
        
        # Update state to completed with response
        self.tm.update_state(task_id, HermesTaskState.COMPLETED, response="Done.")
        self.assertEqual(self.tm.tasks[task_id].state, HermesTaskState.COMPLETED)
        self.assertEqual(self.tm.tasks[task_id].response, "Done.")

    def test_get_executing_tasks(self):
        self.tm.append(TranscriptTurn(
            role="user",
            content="Task 1",
            hermes_task_id="task_1",
            hermes_state=HermesTaskState.PENDING
        ))
        self.tm.append(TranscriptTurn(
            role="user",
            content="Task 2",
            hermes_task_id="task_2",
            hermes_state=HermesTaskState.EXECUTING
        ))
        self.tm.append(TranscriptTurn(
            role="user",
            content="Task 3",
            hermes_task_id="task_3",
            hermes_state=HermesTaskState.COMPLETED
        ))
        
        executing = self.tm.get_executing_tasks()
        self.assertEqual(len(executing), 2)
        task_ids = [t.task_id for t in executing]
        self.assertIn("task_1", task_ids)
        self.assertIn("task_2", task_ids)
        self.assertNotIn("task_3", task_ids)

    def test_cancellation_guard(self):
        self.tm.append(TranscriptTurn(
            role="user",
            content="Task 1",
            hermes_task_id="task_1",
            hermes_state=HermesTaskState.PENDING
        ))
        self.tm.append(TranscriptTurn(
            role="user",
            content="Task 2",
            hermes_task_id="task_2",
            hermes_state=HermesTaskState.EXECUTING
        ))
        
        # Pending task can always be cancelled
        self.assertTrue(self.tm.can_cancel("task_1"))
        # Executing task is cancellable iff the server supports run stop
        # (final rule per 005 T102: Hermes /v1/runs/{id}/stop verified live)
        self.assertTrue(self.tm.can_cancel("task_2"))
        self.assertFalse(self.tm.can_cancel("task_2", server_can_stop=False))
        # Non-existent task cannot be cancelled
        self.assertFalse(self.tm.can_cancel("non_existent"))
        # Terminal tasks can never be cancelled
        self.tm.update_state("task_2", HermesTaskState.FAILED)
        self.assertFalse(self.tm.can_cancel("task_2"))

    def test_extended_task_fields(self):
        task = HermesTask(
            task_id="task_x",
            created_at=time.time(),
            state=HermesTaskState.PENDING,
            request_text="check my email",
            session_id="sess-1",
        )
        self.assertIsNone(task.run_id)
        self.assertFalse(task.delivered)
        self.assertEqual(task.updated_at, task.created_at)
        self.assertFalse(task.is_terminal())
        # 004-era aliases stay wired to the new field names
        self.assertEqual(task.transcript, "check my email")
        task.response = "you have 3 new messages"
        self.assertEqual(task.result_text, "you have 3 new messages")
        task.state = HermesTaskState.FAILED
        self.assertTrue(task.is_terminal())

    def test_sliding_window_preserves_executing_tasks(self):
        # Add an executing task turn first
        self.tm.append(TranscriptTurn(
            role="user",
            content="Run long task",
            hermes_task_id="task_long",
            hermes_state=HermesTaskState.EXECUTING
        ))
        
        # Now append 6 more turns. Normally, the first turn (index 0) would be pruned.
        # But since it has an active executing task, it must be preserved.
        for i in range(6):
            self.tm.append(TranscriptTurn(role="user", content=f"Turn {i}"))
            
        window = self.tm.get_window()
        # To maintain sliding window rules while keeping task_long,
        # we expect length to be 5, but task_long must be present in the window
        task_turns = [t for t in window if t.hermes_task_id == "task_long"]
        self.assertEqual(len(task_turns), 1)
        self.assertEqual(task_turns[0].content, "Run long task")
