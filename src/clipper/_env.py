"""Environment bootstrapping for clipper.

Two responsibilities:
1. Ensure torchcodec can dlopen ffmpeg shared libs. glibc reads LD_LIBRARY_PATH
   once at process start and caches it, so modifying os.environ later doesn't
   work — we re-exec Python with the env var set if needed.
2. Set Marlin / qwen-vl-utils env vars (FPS, max pixels, etc.) before any
   transformers/torch imports happen elsewhere in the package.

Import this module FIRST. It does its work at import time and is a no-op
on subsequent imports.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FFMPEG_LIB = os.environ.get("VIDPROC_FFMPEG_LIB", "/home/elstruck/miniconda3/lib")


def _ensure_ffmpeg_libs() -> None:
    if not Path(FFMPEG_LIB).exists():
        return
    if os.environ.get("_VIDPROC_LD_FIXED"):
        return
    current = os.environ.get("LD_LIBRARY_PATH", "")
    if FFMPEG_LIB in current.split(":"):
        return
    new_env = dict(os.environ)
    new_env["LD_LIBRARY_PATH"] = f"{FFMPEG_LIB}:{current}" if current else FFMPEG_LIB
    new_env["_VIDPROC_LD_FIXED"] = "1"
    os.execve(sys.executable, [sys.executable] + sys.argv, new_env)


def _set_marlin_defaults() -> None:
    # Read by qwen-vl-utils at first video decode. Marlin's model card recommends:
    os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "torchcodec")
    os.environ.setdefault("FPS", "2.0")
    os.environ.setdefault("FPS_MAX_FRAMES", "240")
    os.environ.setdefault("FPS_MIN_FRAMES", "4")
    os.environ.setdefault("VIDEO_MAX_PIXELS", "200704")


_ensure_ffmpeg_libs()
_set_marlin_defaults()
