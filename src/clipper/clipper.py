"""ffmpeg wrapper for cutting clips from a source video.

Two modes:

- stream_copy=True (default): `-c copy`, near-instantaneous, but cuts land on
  the nearest keyframe ≤ start. Good for previews and most teammate-facing exports.
- stream_copy=False: re-encode with libx264, accurate to the frame. Slower but
  needed when the timeline editor produces sub-keyframe trims.

Output is mp4 with H.264 video and AAC audio. Audio is re-encoded in either mode
(stream-copying mismatched audio codecs across containers is fragile).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def cut_clip(
    source: str | Path,
    start: float,
    end: float,
    out_path: str | Path,
    *,
    stream_copy: bool = True,
    overwrite: bool = True,
) -> Path:
    if end <= start:
        raise ValueError(f"end ({end}) must be greater than start ({start})")
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg not found on PATH")

    duration = end - start
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ff, "-hide_banner", "-loglevel", "error"]
    if overwrite:
        cmd.append("-y")
    # `-ss` before `-i` enables fast input-seek to the nearest keyframe.
    cmd += ["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}"]
    if stream_copy:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
        ]
    cmd += ["-movflags", "+faststart", str(out)]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{res.stderr}")
    return out
