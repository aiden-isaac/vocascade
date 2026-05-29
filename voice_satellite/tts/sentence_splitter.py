"""
Sentence splitter module for separating text into sentence chunks and detecting glitch tags.
"""

import re
from collections import namedtuple

# Named tuple representing a chunk of text to be synthesized
SentenceChunk = namedtuple("SentenceChunk", ["text", "tagged"])

def split_clauses(text: str) -> list[str]:
    """
    Splits text on clause boundaries (,, ;, :, —) if the accumulated segment exceeds 8 words.
    """
    raw_parts = re.split(r'(,|;|:|—)', text)
    segments = []
    current_segment = ""
    
    for part in raw_parts:
        if not part:
            continue
        if part in (',', ';', ':', '—'):
            current_segment += part
            word_count = len(current_segment.split())
            if word_count > 8:
                segments.append(current_segment.strip())
                current_segment = ""
        else:
            current_segment += part
            
    if current_segment.strip():
        segments.append(current_segment.strip())
        
    return segments

def split_sentences(text: str, is_final: bool = True) -> list[SentenceChunk]:
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
    
    sub_chunks = []
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
                sub_chunks.append((content, True))
        else:
            # Split normal text on sentence boundaries
            subparts = re.split(r'(?<=[.!?])\s+', part)
            for sub in subparts:
                content = sub.strip()
                # Filter: must contain at least one alphanumeric character
                if content and any(c.isalnum() for c in content):
                    clauses = split_clauses(content)
                    for cl in clauses:
                        sub_chunks.append((cl, False))

    chunks = []
    for idx, (content, tagged) in enumerate(sub_chunks):
        is_last = (idx == len(sub_chunks) - 1)
        if not is_last or is_final:
            if content and not content[-1] in ('.', '!', '?'):
                if not tagged and content[-1] in (',', ';', ':', '—'):
                    content = content[:-1].strip()
                content += "."
        chunks.append(SentenceChunk(text=content, tagged=tagged))
                     
    return chunks
