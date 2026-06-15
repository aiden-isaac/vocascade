"""
vocascade/session/teardown.py — End-of-session detection (US5 / T230).

Two independent signals end a session after the current reply is spoken, so it
never depends on a small local model reliably emitting a sentinel (FR-062):

  1. The model appends the ``ENDSESSION`` sentinel to its farewell reply.
  2. A deterministic farewell-phrase match on the user's transcript (the backstop).

Either one arms a teardown; re-engaging mid-farewell disarms it. Ported verbatim
from the retired Pipecat ``TeardownInterceptor`` so behavior is unchanged.
"""

import re

TERMINATE_SENTINEL = "ENDSESSION"

# Deterministic farewell phrases (matched against the normalized user transcript).
# Kept to unambiguous "wrap up" phrasings to avoid ending a session by accident.
FAREWELL_PHRASES = (
    "that will be all",
    "that'll be all",
    "that is all",
    "thats all",
    "that's all",
    "thats everything",
    "that's everything",
    "thats it for now",
    "that's it for now",
    "nothing else",
    "we are done",
    "we're done",
    "were done",
    "i am done",
    "i'm done",
    "im done",
    "goodbye",
    "good bye",
    "see you later",
    "talk to you later",
    "good night",
    "goodnight",
)


def normalize(text: str) -> str:
    """Lowercase and strip punctuation, collapsing to bare words for matching."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def contains_sentinel(text: str) -> bool:
    """True if the (possibly streamed/spaced) ENDSESSION sentinel is present."""
    return "endsession" in re.sub(r"[^a-z]", "", text.lower())


def strip_sentinel(text: str) -> str:
    """Remove the termination sentinel so it is never stored or spoken."""
    return re.sub(r"(?i)end\s*session", "", text)


# Phrases normalized once so matching is apostrophe/punctuation-insensitive
# ("that'll be all" → "thatll be all" both in the text and here).
_FAREWELL_NORMALIZED = tuple(sorted({normalize(p) for p in FAREWELL_PHRASES}))


def is_farewell(text: str) -> bool:
    """Deterministic backstop: True if the user clearly signalled wrap-up."""
    norm = normalize(text)
    if not norm:
        return False
    return any(phrase in norm for phrase in _FAREWELL_NORMALIZED)
