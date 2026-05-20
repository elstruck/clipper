"""Generate a horizontal thumbnail sprite for a video.

One image per video at `data/thumbs/<video_id>.jpg`. Used by the timeline
clip editor as a visual strip. Lazily generated on first request and
cached forever — re-uploading replaces the underlying file, the sprite
is fine as a one-shot artifact.

Sprite layout: N thumbnails stacked left-to-right, each `THUMB_W` × `THUMB_H`.
N is computed so that overall width stays under MAX_W and time resolution
stays under MAX_INTERVAL.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from clipper.chunker import probe_duration
from clipper.storage import DATA_ROOT

THUMBS_DIR = DATA_ROOT / "thumbs"
THUMB_W = 160
THUMB_H = 90
MIN_INTERVAL = 2.0       # never more than 1 thumb per 2s
MAX_INTERVAL = 60.0      # never less than 1 thumb per 60s
MAX_W = 12800            # cap sprite width — 80 thumbs at 160px

_gen_locks: dict[str, threading.Lock] = {}
_gen_locks_mu = threading.Lock()


def _lock_for(video_id: str) -> threading.Lock:
    with _gen_locks_mu:
        lock = _gen_locks.get(video_id)
        if lock is None:
            lock = threading.Lock()
            _gen_locks[video_id] = lock
        return lock


@dataclass(slots=True, frozen=True)
class SpriteSpec:
    count: int
    interval: float
    width: int
    height: int


def plan_sprite(duration: float) -> SpriteSpec:
    if duration <= 0:
        raise ValueError(f"non-positive duration: {duration}")
    # Pick interval so we land in [MIN_INTERVAL, MAX_INTERVAL] and width <= MAX_W.
    target_count = min(MAX_W // THUMB_W, max(8, int(duration // MIN_INTERVAL)))
    interval = max(MIN_INTERVAL, min(MAX_INTERVAL, duration / max(target_count, 1)))
    count = max(1, int(duration / interval))
    return SpriteSpec(count=count, interval=interval, width=count * THUMB_W, height=THUMB_H)


def sprite_path(video_id: str) -> Path:
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    return THUMBS_DIR / f"{video_id}.jpg"


def generate_sprite(video_id: str, source_path: str | Path, duration: float | None = None) -> SpriteSpec:
    """Generate (or reuse) the sprite for `video_id`. Thread-safe."""
    out = sprite_path(video_id)
    lock = _lock_for(video_id)
    with lock:
        if duration is None:
            duration = probe_duration(source_path)
        spec = plan_sprite(duration)
        if out.exists():
            return spec
        ff = shutil.which("ffmpeg")
        if not ff:
            raise RuntimeError("ffmpeg not found on PATH")

        # fps filter rate = thumbs/sec; tile=NxM lays them into one image.
        fps_rate = 1.0 / spec.interval
        vf = (
            f"fps={fps_rate:.6f},"
            f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase,"
            f"crop={THUMB_W}:{THUMB_H},"
            f"tile={spec.count}x1"
        )
        cmd = [
            ff, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source_path),
            "-frames:v", "1",
            "-vf", vf,
            "-q:v", "5",
            str(out),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg sprite gen failed: {res.stderr}")
        return spec
