import asyncio
import logging
from typing import AsyncIterator, Dict, List
from voice_satellite.tts.sentence_splitter import SentenceChunk
from voice_satellite.tts.genie_client import GenieTTSClient

logger = logging.getLogger("voice_satellite.tts.manager")

class TTSTaskManager:
    """
    Manages parallel TTS synthesis tasks, buffering and yielding audio chunks
    in strict sequence order.
    """
    def __init__(self):
        self._queues: Dict[int, asyncio.Queue] = {}
        self._chunks: Dict[int, SentenceChunk] = {}
        self._tasks: List[asyncio.Task] = []
        self._sequence_counter = 0
        self._completed = False
        self._new_queue_event = asyncio.Event()

    def enqueue_tts(self, chunk: SentenceChunk, tts_client: GenieTTSClient, session_id: str) -> int:
        """
        Enqueues a sentence chunk for synthesis in the background.
        Returns the sequence number allocated to this chunk.
        """
        seq_num = self._sequence_counter
        self._sequence_counter += 1
        
        queue = asyncio.Queue()
        self._queues[seq_num] = queue
        self._chunks[seq_num] = chunk
        
        # Schedule the synthesis task in the background
        task = asyncio.create_task(
            self._synthesize_worker(seq_num, chunk.text, tts_client, session_id)
        )
        self._tasks.append(task)
        
        # Notify the consumer that a new queue is available
        self._new_queue_event.set()
        self._new_queue_event.clear()
        
        return seq_num

    async def _synthesize_worker(self, seq_num: int, text: str, tts_client: GenieTTSClient, session_id: str):
        try:
            async for audio in tts_client.synthesize(text, session=session_id):
                if audio:
                    await self._queues[seq_num].put(audio)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in TTS synthesis task {seq_num}: {e}")
        finally:
            # Signal end of stream for this sequence number
            if seq_num in self._queues:
                await self._queues[seq_num].put(None)

    def mark_complete(self):
        """
        Signals that no more items will be enqueued.
        """
        self._completed = True
        self._new_queue_event.set()

    async def get_audio_chunks(self) -> AsyncIterator[tuple[SentenceChunk, bytes]]:
        """
        Yields audio chunks sequentially in order of sequence numbers.
        """
        next_seq = 0
        while True:
            # Wait until the next sequence queue is available
            while next_seq not in self._queues:
                if self._completed and next_seq >= self._sequence_counter:
                    return
                await self._new_queue_event.wait()
            
            queue = self._queues[next_seq]
            chunk = self._chunks[next_seq]
            
            while True:
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    break
                yield chunk, item
                queue.task_done()
                
            # Clean up references to save memory
            if next_seq in self._queues:
                del self._queues[next_seq]
            if next_seq in self._chunks:
                del self._chunks[next_seq]
                
            next_seq += 1

    async def stop(self):
        """
        Aborts all background synthesis tasks and clears queues.
        """
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._queues.clear()
        self._chunks.clear()
        self._completed = True
        self._new_queue_event.set()
