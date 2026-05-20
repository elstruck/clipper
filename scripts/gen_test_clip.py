"""Generate a synthetic test clip with ffmpeg for the Phase 0 smoke test.

Produces scripts/_test_clip.mp4: ~20 seconds, 30 fps, with three visually distinct
segments (red testsrc, green testsrc, blue testsrc) concatenated, so the model has
something to caption. Captions won't be semantically meaningful, but the pipeline
runs end-to-end.

Usage:
    uv run python scripts/gen_test_clip.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).parent / "_test_clip.mp4"


def main() -> int:
    ff = shutil.which("ffmpeg")
    if not ff:
        print("ERROR: ffmpeg not found on PATH", file=sys.stderr)
        return 1

    OUT.unlink(missing_ok=True)

    # 20-second 480p clip: 0-7s test pattern, 7-14s color bars, 14-20s mandelbrot.
    cmd = [
        ff,
        "-y",
        "-f", "lavfi", "-i", "testsrc=duration=7:size=640x360:rate=30",
        "-f", "lavfi", "-i", "smptebars=duration=7:size=640x360:rate=30",
        "-f", "lavfi", "-i", "mandelbrot=size=640x360:rate=30",
        "-filter_complex",
        "[2:v]trim=duration=6,setpts=PTS-STARTPTS[m];"
        "[0:v][1:v][m]concat=n=3:v=1:a=0[v]",
        "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        str(OUT),
    ]
    print("running:", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("ffmpeg failed:", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        return res.returncode
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
