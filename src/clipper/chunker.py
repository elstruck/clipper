"""Slice a long video into overlapping windows for per-chunk inference.

Marlin's processor (Qwen3VL) doesn't honor `video_start`/`video_end` keys in
the message dict — those are a qwen-vl-utils-only thing. So we extract real
temp files with ffmpeg stream copy (cheap: ~1s for a 90s 360p chunk).

Chunk timestamps end up offset by up to one keyframe-interval (typically 1–10s
for real videos) because stream copy snaps to the nearest preceding keyframe.
Source videos with sparse keyframes will need re-encoded chunks; toggle that
on `extract_chunk(reencode=True)`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WINDOW_SEC = 90.0  # ~3 min at Marlin's 2 FPS / 240-frame cap, with headroom
DEFAULT_OVERLAP_SEC = 8.0


@dataclass(slots=True, frozen=True)
class Chunk:
    index: int
    start: float  # seconds, global timeline
    end: float


def probe_duration(video_path: str | Path) -> float:
    """Return duration in seconds via ffprobe."""
    ff = shutil.which("ffprobe")
    if not ff:
        raise RuntimeError("ffprobe not found on PATH")
    res = subprocess.run(
        [
            ff, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video_path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(res.stdout)
    return float(data["format"]["duration"])


def extract_chunk(
    source: str | Path,
    chunk: Chunk,
    out_path: str | Path,
    *,
    reencode: bool = False,
) -> Path:
    """Extract `chunk` from `source` to `out_path`.

    `reencode=False` (default): stream-copy via `-c copy` — near-instant but
    snaps to the nearest preceding keyframe.
    `reencode=True`: libx264 veryfast — frame-accurate, ~5–10× slower.

    Audio is dropped (`-an`) since Marlin doesn't use it.
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg not found on PATH")
    duration = chunk.end - chunk.start
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{chunk.start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-an",
    ]
    if reencode:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += [str(out)]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg chunk extract failed for chunk {chunk.index}:\n{res.stderr}")
    return out


def chunk_video(
    duration: float,
    *,
    window: float = DEFAULT_WINDOW_SEC,
    overlap: float = DEFAULT_OVERLAP_SEC,
) -> list[Chunk]:
    """Generate overlapping windows covering [0, duration]."""
    if duration <= 0:
        raise ValueError(f"non-positive duration: {duration}")
    if window <= overlap:
        raise ValueError(f"window ({window}) must exceed overlap ({overlap})")

    # If the whole video fits, return a single chunk.
    if duration <= window:
        return [Chunk(index=0, start=0.0, end=duration)]

    step = window - overlap
    chunks: list[Chunk] = []
    start = 0.0
    i = 0
    while start < duration:
        end = min(start + window, duration)
        chunks.append(Chunk(index=i, start=start, end=end))
        if end >= duration:
            break
        start += step
        i += 1
    return chunks
