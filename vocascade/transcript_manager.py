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
    FAILED = "failed"
    CANCELLED = "cancelled"

TERMINAL_STATES = frozenset(
    {HermesTaskState.COMPLETED, HermesTaskState.FAILED, HermesTaskState.CANCELLED}
)

@dataclass
class HermesTask:
    task_id: str
    created_at: float
    state: HermesTaskState
    request_text: str
    run_id: Optional[str] = None       # server-issued; None until accepted / in chat-fallback
    result_text: Optional[str] = None  # full result once terminal
    session_id: str = ""               # X-Hermes-Session-Id of the dispatching voice session
    delivered: bool = False            # result spoken (or spoken-interrupted)
    updated_at: float = 0.0

    def __post_init__(self):
        if not self.updated_at:
            self.updated_at = self.created_at

    # 004-era aliases, kept so existing call sites/tests keep working.
    @property
    def transcript(self) -> str:
        return self.request_text

    @transcript.setter
    def transcript(self, value: str) -> None:
        self.request_text = value

    @property
    def response(self) -> Optional[str]:
        return self.result_text

    @response.setter
    def response(self, value: Optional[str]) -> None:
        self.result_text = value

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

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
                    request_text=turn.content
                )
            else:
                self.tasks[turn.hermes_task_id].state = state
                self.tasks[turn.hermes_task_id].updated_at = time.time()

        self._prune_window()

    def update_state(self, task_id: str, new_state: HermesTaskState, response: Optional[str] = None) -> None:
        """Updates the state of a Hermes task and its corresponding turn in history."""
        if task_id in self.tasks:
            self.tasks[task_id].state = new_state
            self.tasks[task_id].updated_at = time.time()
            if response:
                self.tasks[task_id].result_text = response

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

    def can_cancel(self, task_id: str, server_can_stop: bool = True) -> bool:
        """
        Cancellation guard logic (final rule per 005 T102 contract pinning):
        pending tasks are always cancellable; executing tasks are cancellable
        iff the server supports run stop (Hermes advertises features.run_stop
        and POST /v1/runs/{id}/stop was verified live). Terminal tasks and
        unknown ids are never cancellable.
        """
        if task_id not in self.tasks:
            return False
        state = self.tasks[task_id].state
        if state == HermesTaskState.PENDING:
            return True
        if state == HermesTaskState.EXECUTING:
            return server_can_stop
        return False

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
