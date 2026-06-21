"""
vocascade/tts/chunker.py — shared text→speech chunking conveyor.

Everything spoken (skill replies, Hermes deltas) goes through here before TTS so
synthesis starts on the first sentence instead of buffering the whole reply.
A skill that returns a paragraph as one string used to hit TTS as one block; the
same segment logic that already streamed Hermes now streams those too.

Two entry points:
  - ``SpeechChunker`` — stateful, for streaming sources (feed deltas, flush at end).
  - ``split_for_speech`` — one-shot, for a complete string.
"""

import re

# A speakable segment ends at a sentence terminator OR a list-item/line break.
# Replies are often markdown (headers, bullets, times) with few sentence enders,
# so newlines are first-class boundaries — otherwise the whole reply buffers and
# hits TTS as one giant block.
_SEGMENT_BOUNDARY = re.compile(r"(?<=[.!?:])\s+|\n+")
# Coalesce tiny fragments up to ~a sentence so we don't fire a TTS call per word,
# but flush a long run so no single TTS call swells back into the old problem.
MIN_SEGMENT_CHARS = 40
MAX_SEGMENT_CHARS = 220


def drain_segments(buffer: str):
    """Split a growing buffer into (complete_segments, trailing_remainder).

    A segment is terminated by a sentence ender (. ! ? :) followed by whitespace,
    or by a line break. The unterminated tail stays buffered until more text
    arrives (or the stream ends).
    """
    parts = _SEGMENT_BOUNDARY.split(buffer)
    if len(parts) <= 1:
        return [], buffer
    return parts[:-1], parts[-1]


def clean_for_speech(segment: str) -> str:
    """Strip markdown, list markers, emoji, and odd symbols so TTS speaks the
    content naturally (and faster) instead of reading '**', '-', or '✅' aloud."""
    s = re.sub(r"[*_`#>\[\]]+", "", segment)          # markdown emphasis/headers
    s = s.replace("—", ", ").replace("–", ", ")        # em/en dash → spoken pause
    s = re.sub(r"[^\x00-\x7f]+", "", s)                # drop emoji / non-ASCII
    s = re.sub(r"^[\s\-•*.,]+", "", s)                 # leading bullet/marker noise
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)             # no space before punctuation
    return s


class SpeechChunker:
    """Accumulates streamed text and yields cleaned, speakable chunks.

    Feed it text as it arrives (``feed``); it returns whatever is ready to speak
    now. Call ``flush`` once the source is exhausted to emit the remainder.
    """

    def __init__(self, min_chars: int = MIN_SEGMENT_CHARS, max_chars: int = MAX_SEGMENT_CHARS):
        self._min = min_chars
        self._max = max_chars
        self._buffer = ""    # unterminated tail (no boundary seen yet)
        self._pending = ""   # cleaned text coalesced but not yet flushed

    def feed(self, text: str) -> list[str]:
        """Add ``text`` to the stream; return chunks ready to speak now."""
        out: list[str] = []
        self._buffer += text
        complete, self._buffer = drain_segments(self._buffer)
        for seg in complete:
            cleaned = clean_for_speech(seg)
            if not cleaned:
                continue
            self._pending = f"{self._pending} {cleaned}".strip() if self._pending else cleaned
            # Force-flush an over-long run at a word boundary.
            while len(self._pending) >= self._max:
                cut = self._pending.rfind(" ", 0, self._max)
                cut = cut if cut > 0 else self._max
                head, self._pending = self._pending[:cut].strip(), self._pending[cut:].strip()
                if head:
                    out.append(head)
            # Otherwise flush once it's a full thought (sentence end) or big enough
            # to be worth a TTS call.
            if self._pending and (self._pending[-1] in ".!?:" or len(self._pending) >= self._min):
                out.append(self._pending)
                self._pending = ""
        return out

    def flush(self) -> list[str]:
        """Source exhausted: emit the buffered remainder plus anything pending."""
        out: list[str] = []
        cleaned = clean_for_speech(self._buffer)
        self._buffer = ""
        if cleaned:
            self._pending = f"{self._pending} {cleaned}".strip() if self._pending else cleaned
        if self._pending:
            if self._pending[-1] not in ".!?:":
                self._pending += "."
            out.append(self._pending)
            self._pending = ""
        return out


def split_for_speech(text: str) -> list[str]:
    """One-shot: split a complete string into speakable chunks (skill replies)."""
    if not text:
        return []
    chunker = SpeechChunker()
    return chunker.feed(text) + chunker.flush()


def _demo() -> None:
    # Streaming: deltas split mid-sentence still produce whole spoken segments.
    c = SpeechChunker()
    got = c.feed("The weather is su") + c.feed("nny today. Enjoy it") + c.flush()
    assert got == ["The weather is sunny today.", "Enjoy it."], got

    # One-shot paragraph → multiple chunks, first one short-circuits TTS wait.
    para = split_for_speech("Timer set. I'll let you know in five minutes.")
    assert para == ["Timer set.", "I'll let you know in five minutes."], para

    # Single sentence with no trailing punctuation gets one terminated chunk.
    assert split_for_speech("It is 3:45 PM") == ["It is 3:45 PM."]

    # Markdown / emoji is stripped, not spoken.
    assert split_for_speech("- **Water the plants** 🌱") == ["Water the plants."]

    # Empty / junk → nothing.
    assert split_for_speech("") == []
    assert split_for_speech("   ") == []
    print("chunker demo OK")


if __name__ == "__main__":
    _demo()
