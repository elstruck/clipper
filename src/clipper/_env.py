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


def _nvidia_cu12_lib_dirs() -> list[str]:
    """Find cu12 nvidia .so paths inside the venv so ctranslate2 can dlopen them.

    faster-whisper / ctranslate2 link against libcublas.so.12 and libcudnn.so.9,
    which the `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` packages ship under
    `<site-packages>/nvidia/<lib>/lib/`. They aren't auto-added to LD_LIBRARY_PATH.
    """
    out: list[str] = []
    base = Path(sys.executable).resolve().parents[1] / "lib"
    for child in base.glob("python*/site-packages/nvidia/*/lib"):
        if any(child.glob("*.so*")):
            out.append(str(child))
    return out


def _ensure_runtime_libs() -> None:
    if os.environ.get("_VIDPROC_LD_FIXED"):
        return

    needed: list[str] = []
    if Path(FFMPEG_LIB).exists():
        needed.append(FFMPEG_LIB)
    needed.extend(_nvidia_cu12_lib_dirs())

    if not needed:
        return

    current = os.environ.get("LD_LIBRARY_PATH", "")
    current_parts = current.split(":") if current else []
    missing = [p for p in needed if p not in current_parts]
    if not missing:
        return

    new_env = dict(os.environ)
    new_env["LD_LIBRARY_PATH"] = ":".join(missing + current_parts)
    new_env["_VIDPROC_LD_FIXED"] = "1"
    os.execve(sys.executable, [sys.executable] + sys.argv, new_env)


def _set_marlin_defaults() -> None:
    # Read by qwen-vl-utils at first video decode. Marlin's model card recommends:
    os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "torchcodec")
    os.environ.setdefault("FPS", "2.0")
    os.environ.setdefault("FPS_MAX_FRAMES", "240")
    os.environ.setdefault("FPS_MIN_FRAMES", "4")
    os.environ.setdefault("VIDEO_MAX_PIXELS", "200704")


_ensure_runtime_libs()
_set_marlin_defaults()
