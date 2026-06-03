"""
Transcript manager for the Pipecat Voice Adapter.
Maintains tagged sliding window conversation history and execution state tracking.
"""

from enum import Enum
from dataclasses import dataclass
from typing import List, Optional
import time

class HermesTaskState(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

@dataclass
class HermesTask:
    task_id: str
    created_at: float
    state: HermesTaskState
    transcript: str
    response: Optional[str] = None

@dataclass
class TranscriptTurn:
    role: str  # "user", "assistant", "system"
    content: str
    hermes_task_id: Optional[str] = None
    hermes_state: Optional[HermesTaskState] = None
    timestamp: float = 0.0
    was_interrupted: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

class TranscriptManager:
    def __init__(self, max_turns: int = 7):
        self.max_turns = max_turns
        self.turns: List[TranscriptTurn] = []
        self.tasks: dict[str, HermesTask] = {}

    def append(self, turn: TranscriptTurn) -> None:
        """Appends a new turn to the history and prunes the sliding window."""
        self.turns.append(turn)
        
        # If the turn includes a task ID, track/update it
        if turn.hermes_task_id:
            state = turn.hermes_state or HermesTaskState.PENDING
            if turn.hermes_task_id not in self.tasks:
                self.tasks[turn.hermes_task_id] = HermesTask(
                    task_id=turn.hermes_task_id,
                    created_at=turn.timestamp,
                    state=state,
                    transcript=turn.content
                )
            else:
                self.tasks[turn.hermes_task_id].state = state

        self._prune_window()

    def update_state(self, task_id: str, new_state: HermesTaskState, response: Optional[str] = None) -> None:
        """Updates the state of a Hermes task and its corresponding turn in history."""
        if task_id in self.tasks:
            self.tasks[task_id].state = new_state
            if response:
                self.tasks[task_id].response = response

        # Also update the corresponding turn in the sliding window
        for turn in self.turns:
            if turn.hermes_task_id == task_id:
                turn.hermes_state = new_state

    def get_window(self) -> List[TranscriptTurn]:
        """Returns the current pruned sliding window of turns."""
        return self.turns

    def get_executing_tasks(self) -> List[HermesTask]:
        """Returns all tasks currently in executing or pending state."""
        return [
            task for task in self.tasks.values()
            if task.state in (HermesTaskState.PENDING, HermesTaskState.EXECUTING)
        ]

    def can_cancel(self, task_id: str) -> bool:
        """
        Cancellation guard logic:
        True if the task is pending, False if the task is already executing or done.
        """
        if task_id not in self.tasks:
            return False
        return self.tasks[task_id].state == HermesTaskState.PENDING

    def _prune_window(self) -> None:
        """
        Prunes the window if it exceeds max_turns.
        Never prunes any turns that contain in-flight/executing/pending tasks.
        """
        if len(self.turns) <= self.max_turns:
            return

        # Identify which turns we must keep because they contain executing/pending tasks
        keep_indices = set()
        for idx, turn in enumerate(self.turns):
            if turn.hermes_task_id and turn.hermes_task_id in self.tasks:
                task = self.tasks[turn.hermes_task_id]
                if task.state in (HermesTaskState.PENDING, HermesTaskState.EXECUTING):
                    keep_indices.add(idx)

        # We need to prune starting from the oldest (index 0)
        # Let's count how many turns we need to remove
        excess = len(self.turns) - self.max_turns
        pruned_turns = []
        removed_count = 0

        for idx, turn in enumerate(self.turns):
            if idx in keep_indices:
                pruned_turns.append(turn)
            elif removed_count < excess:
                # Prune this turn
                removed_count += 1
            else:
                pruned_turns.append(turn)

        self.turns = pruned_turns
