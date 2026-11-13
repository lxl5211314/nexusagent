"""Structure-aware chunking.

Markdown headings define the primary boundary; within a section we pack whole
sentences up to ``chunk_size`` characters with a character overlap. Boundaries
are never cut mid-sentence unless a single sentence exceeds the budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.text import split_sentences

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(slots=True)
class ChunkDraft:
    ordinal: int
    heading: str
    content: str

    @property
    def tokens(self) -> int:
        # ~1.6 chars per token for mixed zh/en; good enough for sizing displays.
        return max(1, int(len(self.content) / 1.6))


def _sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            sections.append((match.group(2).strip(), [line]))
        else:
            sections[-1][1].append(line)
    return [(heading, "\n".join(lines).strip()) for heading, lines in sections if "\n".join(lines).strip()]


def chunk_text(text: str, chunk_size: int = 420, overlap: int = 80) -> list[ChunkDraft]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    overlap = max(0, min(overlap, chunk_size // 2))

    drafts: list[ChunkDraft] = []
    for heading, body in _sections(text):
        if len(body) <= chunk_size:
            drafts.append(ChunkDraft(len(drafts), heading, body))
            continue

        buffer = ""
        for sentence in split_sentences(body):
            if len(sentence) > chunk_size:
                # Hard-split an over-long sentence, keeping the overlap tail.
                start = 0
                while start < len(sentence):
                    piece = sentence[start : start + chunk_size]
                    if buffer:
                        buffer = f"{buffer}\n{piece}"
                    else:
                        buffer = piece
                    drafts.append(ChunkDraft(len(drafts), heading, buffer.strip()))
                    buffer = sentence[max(0, start + chunk_size - overlap) : start + chunk_size]
                    start += chunk_size
                buffer = ""
                continue

            candidate = f"{buffer}\n{sentence}" if buffer else sentence
            if len(candidate) <= chunk_size:
                buffer = candidate
            else:
                drafts.append(ChunkDraft(len(drafts), heading, buffer.strip()))
                tail = buffer[-overlap:] if overlap else ""
                buffer = f"{tail}\n{sentence}" if tail else sentence

        if buffer.strip():
            drafts.append(ChunkDraft(len(drafts), heading, buffer.strip()))

    return [d for d in drafts if d.content]
