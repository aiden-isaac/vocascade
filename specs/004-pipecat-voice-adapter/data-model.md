# Data Model: Pipecat Voice Adapter

**Branch**: `pipecat` | **Date**: 2026-06-02

## Core Entities

### AdapterSession

Central runtime state for a single voice interaction session.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | Unique session identifier (UUID hex, 8 chars) |
| `pipeline_state` | `PipelineState` | Current pipeline status (idle, listening, processing, speaking) |
| `active_tasks` | `dict[str, HermesTask]` | Map of `hermes_task_id` → `HermesTask` for in-flight dispatches |
| `cache_warm` | `bool` | Whether the pre-fetch cache has completed initial hydration |
| `is_offline` | `bool` | Whether the system is in offline mode |
| `created_at` | `datetime` | Session creation timestamp |

### HermesTask

A dispatched task tracked through its lifecycle.

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `str` | Format: `task_YYYYMMDD_HHMMSS_XX` (XX = zero-padded counter) |
| `state` | `HermesTaskState` | Enum: `pending`, `executing`, `completed`, `cancelled` |
| `original_transcript` | `str` | The user's original request text |
| `response_text` | `str | None` | Response text from Hermes (populated on completion) |
| `created_at` | `datetime` | Task creation timestamp |
| `completed_at` | `datetime | None` | Task completion timestamp |

### TranscriptTurn

A single turn in the conversation sliding window.

| Field | Type | Description |
|-------|------|-------------|
| `role` | `str` | One of: `user`, `assistant`, `system` |
| `content` | `str` | The text content of the turn |
| `hermes_task_id` | `str | None` | Associated task ID (if Hermes dispatch) |
| `hermes_state` | `HermesTaskState | None` | Current task state (if Hermes dispatch) |
| `timestamp` | `datetime` | Turn creation timestamp |
| `was_interrupted` | `bool` | Whether this turn was interrupted by barge-in |

### ContextSnapshot

Merged pre-fetch cache state returned by `get_context()`.

| Field | Type | Description |
|-------|------|-------------|
| `user_profile` | `dict` | User profile data from `~/.hermes/memory/profile.json` |
| `recent_memories` | `list[str]` | Recent Honcho-synthesized memories |
| `pending_tasks` | `list[dict]` | Summary of pending/executing Hermes tasks |
| `last_updated` | `datetime` | Most recent cache update timestamp |

### OfflineQueueEntry

A deferred task stored in the disk-backed queue.

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | `str` | ISO 8601 timestamp of when the command was received |
| `transcript` | `str` | The user's original spoken text |
| `classification` | `str` | `state_changing` or `deferrable` |
| `status` | `str` | `queued` or `executed` |

### MorningBriefing

Structured summary generated from queued tasks at system wake.

| Field | Type | Description |
|-------|------|-------------|
| `generated_at` | `str` | ISO 8601 timestamp of briefing generation |
| `total_queued` | `int` | Number of tasks that were queued during offline period |
| `tasks` | `list[OfflineQueueEntry]` | The queued task entries |
| `summary_text` | `str` | Human-readable summary for Qwen to read to the user |

## Enumerations

### PipelineState

```
idle → listening → processing → speaking → idle
                                         ↘ interrupted → listening
```

Values: `idle`, `listening`, `processing`, `speaking`, `interrupted`

### HermesTaskState

```
pending → executing → completed
                   ↘ cancelled
```

Values: `pending`, `executing`, `completed`, `cancelled`

## Relationships

```
AdapterSession 1──* HermesTask
AdapterSession 1──1 ContextSnapshot (via PreFetchCache)
TranscriptManager 1──* TranscriptTurn
TranscriptTurn *──0..1 HermesTask (via hermes_task_id)
OfflineHandler 1──* OfflineQueueEntry
OfflineHandler 1──0..1 MorningBriefing
```

## State Transitions

### HermesTask Lifecycle
```
[created] → pending → executing → completed
                              ↘ cancelled (barge-in or user request)
```

### Pipeline State Machine
```
idle ──(wake word)──→ listening
listening ──(speech end)──→ processing
processing ──(direct answer)──→ speaking
processing ──(hermes dispatch)──→ idle (with acknowledgment)
speaking ──(audio complete)──→ listening (if active session)
speaking ──(barge-in)──→ interrupted → listening
listening ──(silence timeout)──→ idle
```
