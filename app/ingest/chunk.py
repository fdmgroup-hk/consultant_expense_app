"""Turn extracted segments into retrieval-sized chunks.

Two passes: oversized segments are split on paragraph/sentence boundaries with a
little overlap, then consecutive small segments (a three-bullet slide, say) are
packed together so a chunk carries enough context to answer a question.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Segment


@dataclass
class Chunk:
    ordinal: int
    locator: str
    heading: str
    text: str


def _split_long(text: str, target: int, overlap: int) -> list[str]:
    if len(text) <= target:
        return [text]

    # Prefer paragraph boundaries, fall back to sentence boundaries.
    units = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if any(len(u) > target for u in units):
        units = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

    pieces: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if len(candidate) > target and current:
            pieces.append(current)
            tail = current[-overlap:] if overlap > 0 else ""
            current = f"{tail}\n\n{unit}".strip() if tail else unit
        else:
            current = candidate
    if current.strip():
        pieces.append(current)

    # A single unit can still exceed the target (a wall of text with no breaks).
    final: list[str] = []
    for piece in pieces:
        while len(piece) > target * 2:
            final.append(piece[:target])
            piece = piece[max(0, target - overlap):]
        final.append(piece)
    return [p.strip() for p in final if p.strip()]


def _combine_locators(locators: list[str]) -> str:
    unique = list(dict.fromkeys(loc for loc in locators if loc))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    # "Slide 3", "Slide 4" -> "Slides 3-4"
    prefixes = {loc.split()[0] for loc in unique if " " in loc}
    numbers = [loc.split()[-1] for loc in unique if " " in loc]
    if len(prefixes) == 1 and all(n.isdigit() for n in numbers) and len(numbers) == len(unique):
        prefix = unique[0].split()[0]
        return f"{prefix}s {numbers[0]}-{numbers[-1]}"
    return "; ".join(unique[:3])


#: A chunk never spans more than this many source segments, so a citation stays
#: precise. "Slide 7" is a useful answer to "where did that come from"; "Slides
#: 1-14" is not, however well it retrieves.
MAX_SEGMENTS_PER_CHUNK = 3


def chunk_segments(
    segments: list[Segment],
    target_chars: int = 1100,
    overlap_chars: int = 180,
) -> list[Chunk]:
    # A segment with this much text stands on its own; only genuinely thin ones
    # (a three-bullet slide) get merged with their neighbours for context.
    min_standalone = max(300, target_chars // 3)

    # Pass 1 - split anything oversized. A section that splits gets numbered
    # locators ("Stage 7 (2/3)") so two citations from one long section are
    # tellable apart in the sources list.
    expanded: list[Segment] = []
    for segment in segments:
        pieces = _split_long(segment.text, target_chars, overlap_chars)
        for index, piece in enumerate(pieces, start=1):
            locator = segment.locator
            if len(pieces) > 1 and locator:
                locator = f"{locator} ({index}/{len(pieces)})"
            expanded.append(Segment(locator, segment.heading, piece))

    # Pass 2 - pack small neighbours together.
    chunks: list[Chunk] = []
    buffer: list[Segment] = []

    def rendered(segment: Segment) -> str:
        body = segment.text.strip()
        if segment.heading and segment.heading not in body:
            return f"{segment.heading}\n{body}".strip()
        return body

    def flush() -> None:
        if not buffer:
            return
        body = "\n\n".join(rendered(s) for s in buffer).strip()
        if body:
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    locator=_combine_locators([s.locator for s in buffer]),
                    heading=next((s.heading for s in buffer if s.heading), ""),
                    text=body,
                )
            )
        buffer.clear()

    for segment in expanded:
        if not segment.text.strip():
            continue

        if len(segment.text) >= min_standalone:
            flush()
            buffer.append(segment)
            flush()
            continue

        pending = sum(len(s.text) for s in buffer)
        if buffer and (pending + len(segment.text) > target_chars
                       or len(buffer) >= MAX_SEGMENTS_PER_CHUNK):
            flush()
        buffer.append(segment)

    flush()
    return chunks
