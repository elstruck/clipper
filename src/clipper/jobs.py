"""Background worker for GPU-bound jobs (indexing, search fanout).

Single-worker design: the model lives on one GPU, so parallel inference
buys nothing. A dedicated daemon thread consumes from a queue; submitting
a job returns immediately with a `job_id` that the client polls.

Progress is written to the `jobs` table on every chunk boundary. There's
no SSE plumbing here — Phase 3 will add it on top.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from clipper import db, pipeline
from clipper.chunker import Chunk, probe_duration

log = logging.getLogger(__name__)


@dataclass
class JobRequest:
    job_id: str
    func: Callable[[Callable[[Chunk, int, int], None]], Any]


_queue: queue.Queue[JobRequest] = queue.Queue()
_worker_started = threading.Event()
_worker_thread: Optional[threading.Thread] = None


def _run_worker() -> None:
    log.info("job worker started")
    while True:
        req = _queue.get()
        if req is None:  # type: ignore[unreachable]
            break
        log.info("job %s starting", req.job_id)
        db.update_job(req.job_id, status="running", started_at=time.time())

        def _progress_cb(chunk: Chunk, i: int, total: int) -> None:
            db.update_job(
                req.job_id,
                progress_current=i + 1,
                progress_total=total,
                message=f"chunk {i + 1}/{total} ({chunk.start:.0f}s → {chunk.end:.0f}s)",
            )

        try:
            req.func(_progress_cb)
            db.update_job(req.job_id, status="done", finished_at=time.time())
            log.info("job %s done", req.job_id)
        except Exception as e:
            log.exception("job %s failed", req.job_id)
            db.update_job(
                req.job_id, status="failed",
                finished_at=time.time(), error=f"{type(e).__name__}: {e}",
            )


def ensure_worker() -> None:
    global _worker_thread
    if _worker_started.is_set():
        return
    _worker_thread = threading.Thread(target=_run_worker, daemon=True, name="clipper-worker")
    _worker_thread.start()
    _worker_started.set()


def submit_index_job(video_id: str) -> str:
    """Enqueue an indexing job for `video_id`. Returns the new job_id."""
    ensure_worker()
    video = db.get_video(video_id)
    if not video:
        raise KeyError(f"unknown video: {video_id}")

    job_id = db.create_job(video_id, "index")

    def _job(progress_cb: Callable[[Chunk, int, int], None]) -> None:
        # Probe duration up front so the video row shows it even before chunks finish.
        path = video["path"]
        if not video.get("duration"):
            try:
                duration = probe_duration(path)
                db.update_video(video_id, duration=duration)
            except Exception:
                log.exception("probe_duration failed for %s", path)

        db.update_video(video_id, status="indexing")
        try:
            pipeline.index_video(path, progress=progress_cb)
            db.update_video(video_id, status="indexed", indexed_at=time.time())
        except Exception:
            db.update_video(video_id, status="failed")
            raise

    _queue.put(JobRequest(job_id=job_id, func=_job))
    return job_id


def submit_find_fanout_job(video_id: str, query: str) -> str:
    """Enqueue a find-fanout job. Result spans land in jobs.message as JSON."""
    ensure_worker()
    video = db.get_video(video_id)
    if not video:
        raise KeyError(f"unknown video: {video_id}")
    job_id = db.create_job(video_id, "find_fanout")

    def _job(progress_cb: Callable[[Chunk, int, int], None]) -> None:
        import json
        from dataclasses import asdict
        from clipper import search as search_mod

        idx = pipeline.load_index(video["path"])
        if idx is None:
            raise RuntimeError(f"video {video_id} not indexed yet")
        # The current find_fanout doesn't accept a progress cb — we approximate
        # by reporting just once at the start. (Will refine in a later phase.)
        progress_cb(Chunk(0, 0.0, 0.0), 0, len(idx.get("chunks", [])))
        hits = search_mod.find_fanout(idx, video["path"], query)
        db.update_job(
            job_id,
            result=json.dumps([asdict(h) for h in hits]),
            message=f"{len(hits)} hits",
        )

    _queue.put(JobRequest(job_id=job_id, func=_job))
    return job_id
