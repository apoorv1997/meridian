"""Sentence-boundary chunking.

Splits on sentence boundaries and packs sentences into chunks up to
max_chars, so no chunk ends mid-sentence. Falls back to fixed-size splitting
for pathological single sentences longer than max_chars.
"""

import re

from meridian_rag.chunking.fixed import fixed_size_chunks

# Sentence end: punctuation followed by whitespace and an uppercase letter,
# digit, or end of string. Deliberately simple — no NLP dependency.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(text) if s.strip()]


def semantic_chunks(text: str, max_chars: int = 1000) -> list[str]:
    """Pack whole sentences into chunks of at most max_chars."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        if len(sentence) > max_chars:
            # Flush what we have, then hard-split the oversized sentence.
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            chunks.extend(fixed_size_chunks(sentence, chunk_size=max_chars, overlap=0))
            continue

        # +1 for the joining space
        if current and current_len + 1 + len(sentence) > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [], 0

        current.append(sentence)
        current_len += len(sentence) + (1 if current_len else 0)

    if current:
        chunks.append(" ".join(current))
    return chunks
