"""
Sentence splitter module for separating text into sentence chunks and detecting glitch tags.
"""

import re
from collections import namedtuple

# Named tuple representing a chunk of text to be synthesized
SentenceChunk = namedtuple("SentenceChunk", ["text", "tagged"])

def split_sentences(text: str) -> list[SentenceChunk]:
    """
    Splits text into chunks at sentence boundaries (using (?<=[.!?])\\s+) and glitch tag boundaries.
    Filters out empty/non-alphanumeric chunks and ensures trailing punctuation on each chunk.
    
    Returns a list of SentenceChunk named tuples.
    """
    if not text:
        return []

    # Regex to isolate <glitch>...</glitch> blocks
    pattern = re.compile(r'(<glitch>.*?</glitch>)', re.DOTALL | re.IGNORECASE)
    parts = pattern.split(text)
    
    chunks = []
    for part in parts:
        if not part:
            continue
        
        # Check if it is a glitch tag
        is_glitch = part.lower().startswith("<glitch>") and part.lower().endswith("</glitch>")
        
        if is_glitch:
            # Extract content inside <glitch> and </glitch> tags
            content = part[8:-9].strip()
            # Filter: must contain at least one alphanumeric character
            if content and any(c.isalnum() for c in content):
                # Ensure trailing punctuation
                if not content[-1] in ('.', '!', '?'):
                    content += "."
                chunks.append(SentenceChunk(text=content, tagged=True))
        else:
            # Split normal text on sentence boundaries
            subparts = re.split(r'(?<=[.!?])\s+', part)
            for sub in subparts:
                content = sub.strip()
                # Filter: must contain at least one alphanumeric character
                if content and any(c.isalnum() for c in content):
                    # Ensure trailing punctuation
                    if not content[-1] in ('.', '!', '?'):
                        content += "."
                    chunks.append(SentenceChunk(text=content, tagged=False))
                    
    return chunks
