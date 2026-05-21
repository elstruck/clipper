"""End-to-end indexing pipeline: extract chunks → caption → merge → write JSON.

Output JSON shape (one per video, written next to the video as `<name>.index.json`):

    {
      "video": "path/to/video.mp4",
      "duration": 1234.5,
      "chunks": [{"index": 0, "start": 0.0, "end": 90.0}, ...],
      "scenes": ["...", "...", ...],          # per-chunk scene paragraphs
      "events": [
        {"start": 12.3, "end": 18.7,
         "description": "...", "chunk_index": 0}
      ]
    }
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from clipper import marlin, transcribe as transcribe_mod
from clipper.chunker import (
    Chunk,
    DEFAULT_OVERLAP_SEC,
    DEFAULT_WINDOW_SEC,
    chunk_video,
    extract_chunk,
    probe_duration,
)


def index_path_for(video_path: str | Path) -> Path:
    p = Path(video_path)
    return p.with_suffix(p.suffix + ".index.json")


def _merge_overlap(events: list[dict], step: float) -> list[dict]:
    """Drop events whose midpoint falls inside the next chunk's overlap zone.

    Each chunk N owns events with midpoint in [chunk.start, chunk.start + step].
    Events with midpoints past that boundary are handled by chunk N+1 instead,
    deduplicating without fuzzy text matching. The last chunk keeps everything.
    """
    if not events:
        return []
    by_chunk: dict[int, list[dict]] = {}
    for ev in events:
        by_chunk.setdefault(ev["chunk_index"], []).append(ev)
    max_idx = max(by_chunk)
    kept: list[dict] = []
    for idx, evs in sorted(by_chunk.items()):
        if idx == max_idx:
            kept.extend(evs)
            continue
        chunk_start = evs[0].get("_chunk_start", 0.0)
        cutoff = chunk_start + step
        for ev in evs:
            mid = (ev["start"] + ev["end"]) / 2.0
            if mid < cutoff:
                kept.append(ev)
    return kept


ProgressFn = Callable[[str, int, int], None]
"""progress(message, current, total) — current is 1-indexed steps done."""


def index_video(
    video_path: str | Path,
    *,
    window: float = DEFAULT_WINDOW_SEC,
    overlap: float = DEFAULT_OVERLAP_SEC,
    reencode: bool = False,
    caption: bool = False,
    transcribe: bool = True,
    progress: Optional[ProgressFn] = None,
) -> dict:
    """Run the indexing pipeline on a video.

    Up to two phases, both optional:
    1. Per-chunk Marlin visual captioning (`caption=True`). Off by default —
       Marlin's per-second event captions are mostly noise for talking
       videos. Turn on for visual-driven content (silent screencasts, sports,
       music videos) where the transcript alone won't carry the meaning.
    2. Single-pass Whisper transcription (`transcribe=True`). On by default —
       speech is the highest-signal track for most long-form content.

    At least one phase should be enabled; both off is allowed but produces
    an empty index.
    """
    video_path = str(video_path)
    duration = probe_duration(video_path)
    chunks = chunk_video(duration, window=window, overlap=overlap) if caption else []

    total_steps = (len(chunks) if caption else 0) + (1 if transcribe else 0)

    def _emit(msg: str, current: int) -> None:
        if progress:
            progress(msg, current, total_steps)

    scenes: list[str] = []
    all_events: list[dict] = []

    if caption:
        model = marlin.get_model()
        with tempfile.TemporaryDirectory(prefix="clipper-chunks-") as tmpdir:
            tmproot = Path(tmpdir)
            for c in chunks:
                _emit(
                    f"captioning chunk {c.index + 1}/{len(chunks)} "
                    f"({c.start:.0f}s → {c.end:.0f}s)",
                    c.index + 1,
                )
                chunk_path = tmproot / f"chunk_{c.index:04d}.mp4"
                extract_chunk(video_path, c, chunk_path, reencode=reencode)

                t0 = time.time()
                result = marlin.caption(model, str(chunk_path))
                elapsed = time.time() - t0
                scenes.append(result.scene)

                chunk_len = c.end - c.start
                for ev in result.events:
                    local_start = max(0.0, min(ev["start"], chunk_len))
                    local_end = max(local_start, min(ev["end"], chunk_len))
                    all_events.append({
                        "start": round(c.start + local_start, 3),
                        "end": round(c.start + local_end, 3),
                        "description": ev["description"],
                        "chunk_index": c.index,
                        "_chunk_start": c.start,
                        "_elapsed": round(elapsed, 2),
                    })

                chunk_path.unlink(missing_ok=True)

    step = window - overlap
    merged = _merge_overlap(all_events, step)
    for ev in merged:
        ev.pop("_chunk_start", None)
    merged.sort(key=lambda e: e["start"])

    transcript: Optional[dict] = None
    if transcribe:
        transcribe_step = total_steps  # the last step in the job
        _emit(f"transcribing audio ({duration:.0f}s)", transcribe_step)

        def _trans_progress(cur_sec: float, total_sec: float) -> None:
            _emit(
                f"transcribing {cur_sec:.0f}s of {total_sec:.0f}s",
                transcribe_step,
            )

        try:
            transcript = transcribe_mod.transcribe(video_path, progress=_trans_progress)
        except Exception as e:
            # Don't fail the whole index just because transcription broke
            # (e.g. video has no audio stream).
            transcript = {"error": f"{type(e).__name__}: {e}", "segments": []}

    out = {
        "video": video_path,
        "duration": duration,
        "window": window,
        "overlap": overlap,
        "chunks": [asdict(c) for c in chunks],
        "scenes": scenes,
        "events": merged,
        "transcript": transcript,
        "captioned": caption,
        "transcribed": transcribe,
    }

    out_path = index_path_for(video_path)
    out_path.write_text(json.dumps(out, indent=2))
    return out


def load_index(video_path: str | Path) -> Optional[dict]:
    """Load the cached index for a video, or None if not indexed yet."""
    p = index_path_for(video_path)
    if not p.exists():
        return None
    return json.loads(p.read_text())
