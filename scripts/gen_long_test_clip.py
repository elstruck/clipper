"""Generate a ~4-minute synthetic test video for Phase 1.

Concatenates four visually distinct lavfi sources so the chunker has multiple
real chunks to caption. Won't be semantically interesting, but will exercise
the per-chunk caption + merge code path.

Sections:
    0:00 - 1:00  SMPTE color bars
    1:00 - 2:00  testsrc pattern (the classic test card)
    2:00 - 3:00  mandelbrot fractal
    3:00 - 4:00  RGB test pattern

Usage:
    uv run python scripts/gen_long_test_clip.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).parent / "_long_test_clip.mp4"
SEG = 60  # seconds per section
SIZE = "640x360"
RATE = 30


def main() -> int:
    ff = shutil.which("ffmpeg")
    if not ff:
        print("ERROR: ffmpeg not found on PATH", file=sys.stderr)
        return 1

    OUT.unlink(missing_ok=True)

    cmd = [
        ff, "-y",
        "-f", "lavfi", "-i", f"smptebars=duration={SEG}:size={SIZE}:rate={RATE}",
        "-f", "lavfi", "-i", f"testsrc=duration={SEG}:size={SIZE}:rate={RATE}",
        "-f", "lavfi", "-i", f"mandelbrot=size={SIZE}:rate={RATE}",
        "-f", "lavfi", "-i", f"rgbtestsrc=duration={SEG}:size={SIZE}:rate={RATE}",
        "-filter_complex",
        f"[2:v]trim=duration={SEG},setpts=PTS-STARTPTS[m];"
        "[0:v][1:v][m][3:v]concat=n=4:v=1:a=0[v]",
        "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-g", "30",  # keyframe every 1s so cuts land cleanly
        str(OUT),
    ]
    print("running ffmpeg (~30s) ...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("ffmpeg failed:", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        return res.returncode
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
