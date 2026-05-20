"""Filesystem layout for uploads, clips, and the SQLite db."""

from __future__ import annotations

import os
from pathlib import Path

DATA_ROOT = Path(os.environ.get(
    "CLIPPER_DATA_ROOT",
    Path(__file__).resolve().parents[2] / "data",
))

UPLOADS_DIR = DATA_ROOT / "uploads"
CLIPS_DIR = DATA_ROOT / "clips"
DB_PATH = DATA_ROOT / "clipper.db"


def ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)


def upload_path(video_id: str, ext: str) -> Path:
    ext = ext.lstrip(".").lower()
    return UPLOADS_DIR / f"{video_id}.{ext}"


def clip_path(clip_id: str) -> Path:
    return CLIPS_DIR / f"{clip_id}.mp4"
