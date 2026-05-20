"""SQLite schema + DAO for videos, jobs, and clips.

Events live in `<video>.index.json` next to the source file, not in the DB —
they're already an artifact of the pipeline and re-querying JSON is plenty
fast for the indexed set sizes we expect (a few hundred events per video).
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from clipper.storage import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    duration REAL,
    size_bytes INTEGER,
    status TEXT NOT NULL DEFAULT 'uploaded',   -- uploaded | indexing | indexed | failed
    uploaded_at REAL NOT NULL,
    indexed_at REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    type TEXT NOT NULL,                        -- index | find_fanout
    status TEXT NOT NULL DEFAULT 'queued',     -- queued | running | done | failed
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    message TEXT,                              -- human-readable status
    result TEXT,                               -- JSON result payload, set by job
    error TEXT,
    started_at REAL,
    finished_at REAL,
    created_at REAL NOT NULL,
    FOREIGN KEY(video_id) REFERENCES videos(id)
);

CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    name TEXT,
    start REAL NOT NULL,
    end REAL NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER,
    reencoded INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    FOREIGN KEY(video_id) REFERENCES videos(id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_video ON jobs(video_id);
CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);
"""

_init_lock = threading.Lock()
_initialized = False


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def init_db() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        ensure_dirs()
        with sqlite3.connect(DB_PATH) as conn:
            conn.executescript(SCHEMA)
        _initialized = True


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def new_id() -> str:
    return uuid.uuid4().hex


# -------------------- videos --------------------

def create_video(
    filename: str, path: Path, size_bytes: int, *, video_id: Optional[str] = None,
) -> str:
    vid = video_id or new_id()
    with connect() as c:
        c.execute(
            "INSERT INTO videos (id, filename, path, size_bytes, status, uploaded_at) "
            "VALUES (?, ?, ?, ?, 'uploaded', ?)",
            (vid, filename, str(path), size_bytes, time.time()),
        )
    return vid


def get_video(video_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_videos() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM videos ORDER BY uploaded_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def update_video(video_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [video_id]
    with connect() as c:
        c.execute(f"UPDATE videos SET {cols} WHERE id = ?", values)


def delete_video(video_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM jobs WHERE video_id = ?", (video_id,))
        c.execute("DELETE FROM clips WHERE video_id = ?", (video_id,))
        c.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        return _row_to_dict(row)


# -------------------- jobs --------------------

def create_job(video_id: str, job_type: str) -> str:
    job_id = new_id()
    with connect() as c:
        c.execute(
            "INSERT INTO jobs (id, video_id, type, status, created_at) "
            "VALUES (?, ?, ?, 'queued', ?)",
            (job_id, video_id, job_type, time.time()),
        )
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_jobs(video_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    with connect() as c:
        if video_id:
            rows = c.execute(
                "SELECT * FROM jobs WHERE video_id = ? ORDER BY created_at DESC LIMIT ?",
                (video_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with connect() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id = ?", values)


# -------------------- clips --------------------

def create_clip(
    video_id: str, name: Optional[str], start: float, end: float,
    path: Path, size_bytes: int, reencoded: bool,
    *, clip_id: Optional[str] = None,
) -> str:
    cid = clip_id or new_id()
    with connect() as c:
        c.execute(
            "INSERT INTO clips (id, video_id, name, start, end, path, size_bytes, reencoded, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, video_id, name, start, end, str(path), size_bytes,
             1 if reencoded else 0, time.time()),
        )
    return cid


def get_clip(clip_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_clips(video_id: Optional[str] = None) -> list[dict]:
    with connect() as c:
        if video_id:
            rows = c.execute(
                "SELECT * FROM clips WHERE video_id = ? ORDER BY created_at DESC",
                (video_id,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM clips ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def delete_clip(clip_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        return _row_to_dict(row)
