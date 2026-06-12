import pytest
from pipecat.frames.frames import (
    EndFrame,
    TranscriptionFrame,
    LLMMessagesAppendFrame,
    LLMContextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameProcessor
from voice_adapter.config import load_config
from voice_adapter.transcript_manager import TranscriptManager, TranscriptTurn
from voice_adapter.adapter import (
    AdapterProcessor,
    generate_hermes_task_id,
    _is_farewell,
    _contains_sentinel,
    _strip_sentinel,
)


class FrameCollector(FrameProcessor):
    """Simple processor to collect downstream frames for testing."""
    def __init__(self):
        super().__init__()
        self.pushed_frames = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.pushed_frames.append(frame)
        await self.push_frame(frame, direction)


def test_generate_hermes_task_id():
    task_id1 = generate_hermes_task_id()
    task_id2 = generate_hermes_task_id()
    
    assert task_id1.startswith("task_")
    assert len(task_id1) == 23  # format: task_YYYYMMDD_HHMMSS_XX
    assert task_id1 != task_id2


@pytest.mark.asyncio
async def test_adapter_processor_start_frame():
    config = load_config()
    manager = TranscriptManager()
    processor = AdapterProcessor(transcript_manager=manager, config=config)
    collector = FrameCollector()
    
    pipeline = Pipeline([processor, collector])
    task = PipelineTask(pipeline, enable_rtvi=False)
    
    # StartFrame is automatically queued by PipelineTask on start,
    # so we only queue EndFrame.
    await task.queue_frame(EndFrame())
    
    runner = PipelineRunner()
    await runner.add_workers(task)
    await runner.run()
    
    # Check pushed frames
    appends = [f for f in collector.pushed_frames if isinstance(f, LLMMessagesAppendFrame)]
    assert len(appends) == 1
    assert appends[0].messages[0]["role"] == "system"
    assert "concise" in appends[0].messages[0]["content"]


@pytest.mark.asyncio
async def test_adapter_processor_llm_messages_append_frame():
    config = load_config()
    manager = TranscriptManager()
    processor = AdapterProcessor(transcript_manager=manager, config=config)
    collector = FrameCollector()
    
    pipeline = Pipeline([processor, collector])
    task = PipelineTask(pipeline, enable_rtvi=False)
    
    await task.queue_frame(LLMMessagesAppendFrame(messages=[{"role": "system", "content": "custom system prompt"}]))
    await task.queue_frame(EndFrame())
    
    runner = PipelineRunner()
    await runner.add_workers(task)
    await runner.run()
    
    turns = manager.get_window()
    # It will have system prompt from StartFrame injection, plus custom system prompt
    assert len(turns) == 2
    assert turns[0].role == "system"
    assert turns[1].role == "system"
    assert turns[1].content == "custom system prompt"


@pytest.mark.asyncio
async def test_adapter_processor_transcription_frame():
    config = load_config()
    manager = TranscriptManager()
    processor = AdapterProcessor(transcript_manager=manager, config=config)
    collector = FrameCollector()
    
    pipeline = Pipeline([processor, collector])
    task = PipelineTask(pipeline, enable_rtvi=False)
    
    await task.queue_frame(TranscriptionFrame(text="hello", user_id="1", timestamp="123"))
    await task.queue_frame(EndFrame())
    
    runner = PipelineRunner()
    await runner.add_workers(task)
    await runner.run()
    
    contexts = [f for f in collector.pushed_frames if isinstance(f, LLMContextFrame)]
    assert len(contexts) == 1
    context = contexts[0].context
    # The context should have system message (from start frame) + user message
    assert len(context.messages) == 2
    assert context.messages[0]["role"] == "system"
    assert context.messages[1]["role"] == "user"
    assert context.messages[1]["content"] == "hello"


# ── Session termination detection helpers ────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Okay, that will be all, thanks!",
    "Goodbye.",
    "talk to you later",
    "I'm done for now",
    "Alright, that's everything.",
])
def test_is_farewell_positive(text):
    assert _is_farewell(text) is True


@pytest.mark.parametrize("text", [
    "what is the weather today",
    "tell me a joke",
    "can you set a timer",
    "",
])
def test_is_farewell_negative(text):
    assert _is_farewell(text) is False


@pytest.mark.parametrize("text", [
    "Bye!\nENDSESSION",
    "Take care! END SESSION",
    "see ya endsession",
])
def test_contains_sentinel_positive(text):
    assert _contains_sentinel(text) is True


def test_contains_sentinel_no_false_positive():
    # "end ... session" with other words between must not trigger.
    assert _contains_sentinel("let's end this yoga session early") is False


def test_strip_sentinel_removes_all_variants():
    assert _strip_sentinel("Goodbye ENDSESSION").strip() == "Goodbye"
    assert _strip_sentinel("Goodbye\nEND SESSION").strip() == "Goodbye"
    assert "session" not in _strip_sentinel("done end session").lower()
