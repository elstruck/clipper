"""Two search modes over an indexed video.

- `text_search`: fast keyword/substring ranking over cached caption events.
  Use this for interactive queries when the user is browsing.

- `find_fanout`: precise per-chunk `find()` calls, aggregated into a
  ranked list of spans. Slower (extracts chunk to temp file, then runs the
  model) but more accurate when text search misses due to vocabulary
  mismatch (e.g. user query "argument" vs caption "they raise their voices").
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from clipper import marlin
from clipper.chunker import Chunk, extract_chunk


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(slots=True)
class SearchHit:
    start: float
    end: float
    description: str
    score: float
    chunk_index: int
    source: str = "caption"  # "caption" | "transcript" | "find"


def _score_text(query: str, q_tokens: set[str], text: str) -> float:
    """Return a score >0 if `text` plausibly matches `query`, else 0."""
    t_lower = text.lower()
    q_lower = query.lower().strip()
    if q_tokens:
        t_tokens = set(_tokenize(text))
        overlap = len(q_tokens & t_tokens)
        if overlap == 0 and q_lower not in t_lower:
            return 0.0
        return overlap + (0.5 if q_lower in t_lower else 0.0)
    return 1.0 if q_lower and q_lower in t_lower else 0.0


def text_search(
    index: dict, query: str, *, limit: int = 20,
    include_captions: bool = True, include_transcript: bool = True,
) -> list[SearchHit]:
    """Rank caption events and transcript segments by token overlap with query."""
    q_tokens = set(_tokenize(query))
    hits: list[SearchHit] = []

    if include_captions:
        for ev in index.get("events", []):
            s = _score_text(query, q_tokens, ev["description"])
            if s <= 0:
                continue
            hits.append(SearchHit(
                start=ev["start"], end=ev["end"],
                description=ev["description"], score=s,
                chunk_index=ev.get("chunk_index", 0),
                source="caption",
            ))

    if include_transcript:
        transcript = index.get("transcript") or {}
        for seg in transcript.get("segments") or []:
            s = _score_text(query, q_tokens, seg["text"])
            if s <= 0:
                continue
            hits.append(SearchHit(
                start=seg["start"], end=seg["end"],
                description=seg["text"], score=s,
                chunk_index=-1,
                source="transcript",
            ))

    hits.sort(key=lambda h: (-h.score, h.start))
    return hits[:limit]


def find_fanout(
    index: dict,
    video_path: str,
    query: str,
    *,
    limit: int = 10,
    reencode: bool = False,
) -> list[SearchHit]:
    """Run Marlin's `find()` against every chunk and return the parsed spans.

    Re-extracts chunks to temp files (auto-cleaned). Spans returned by Marlin
    are local to each chunk; we offset to global time. Chunks where the model
    returned no parsable span are skipped.
    """
    model = marlin.get_model()
    chunks_raw = index.get("chunks", [])
    chunks = [Chunk(**c) for c in chunks_raw]
    hits: list[SearchHit] = []
    with tempfile.TemporaryDirectory(prefix="clipper-find-") as tmpdir:
        tmproot = Path(tmpdir)
        for c in chunks:
            chunk_path = tmproot / f"chunk_{c.index:04d}.mp4"
            extract_chunk(video_path, c, chunk_path, reencode=reencode)
            result = marlin.find(model, str(chunk_path), query)
            chunk_path.unlink(missing_ok=True)
            if not result.format_ok or result.span is None:
                continue
            local_start, local_end = result.span
            chunk_len = c.end - c.start
            ls = max(0.0, min(local_start, chunk_len))
            le = max(ls, min(local_end, chunk_len))
            # Confidence heuristic: Marlin doesn't surface logprobs, but when it
            # has no real match it tends to return a span covering most of the
            # chunk. Penalize spans whose length approaches the chunk length.
            span_len = le - ls
            coverage = span_len / chunk_len if chunk_len > 0 else 1.0
            score = max(0.0, 1.0 - coverage)
            hits.append(SearchHit(
                start=round(c.start + ls, 3),
                end=round(c.start + le, 3),
                description=result.raw,
                score=round(score, 3),
                chunk_index=c.index,
                source="find",
            ))
    hits.sort(key=lambda h: (-h.score, h.start))
    return hits[:limit]
