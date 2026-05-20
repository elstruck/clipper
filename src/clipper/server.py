"""FastAPI app — upload, index, search, cut, list.

Single-worker by design (the model is GPU-bound). Long-running work goes
through `jobs.py`; HTTP endpoints just enqueue and return immediately.

Run with: `clip serve` or `uv run uvicorn clipper.server:app --host 0.0.0.0 --port 8000`.
"""

from __future__ import annotations

# Import the package first so LD_LIBRARY_PATH + Marlin env vars are set
# before transformers/torch get loaded by any sub-import.
import clipper  # noqa: F401

import logging
import mimetypes
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from clipper import clipper as clipper_mod
from clipper import db, jobs, pipeline
from clipper import search as search_mod
from clipper.storage import CLIPS_DIR, UPLOADS_DIR, clip_path, ensure_dirs, upload_path

log = logging.getLogger("clipper.server")

ALLOWED_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
UPLOAD_CHUNK_BYTES = 1 << 20  # 1 MiB


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    db.init_db()
    jobs.ensure_worker()
    log.info("clipper server ready (data root: %s)", UPLOADS_DIR.parent)
    yield


app = FastAPI(title="Marlin Clipper", version="0.2.0", lifespan=lifespan)


# ---------------- models ----------------

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    fanout: bool = False
    limit: int = 20


class ClipRequest(BaseModel):
    video_id: str
    start: float = Field(..., ge=0)
    end: float
    name: Optional[str] = None
    reencode: bool = False


# ---------------- helpers ----------------

def _video_or_404(video_id: str) -> dict:
    v = db.get_video(video_id)
    if v is None:
        raise HTTPException(404, f"video not found: {video_id}")
    return v


def _byte_range_response(path: Path, range_header: Optional[str]) -> Response:
    """Serve `path` with HTTP Range support so <video> can scrub."""
    file_size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    if range_header is None:
        return FileResponse(path, media_type=media_type)

    # Parse "bytes=START-END". END may be empty.
    try:
        units, rng = range_header.split("=", 1)
        if units.strip().lower() != "bytes":
            raise ValueError
        start_str, end_str = rng.split("-", 1)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
    except ValueError:
        raise HTTPException(416, "invalid Range header")

    if start >= file_size or end >= file_size or start > end:
        raise HTTPException(416, "Range not satisfiable")

    length = end - start + 1

    def _iter():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return Response(content=b"".join(_iter()), status_code=206,
                    media_type=media_type, headers=headers)


# ---------------- routes ----------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ----- videos -----

@app.post("/api/videos")
async def upload_video(file: UploadFile = File(...)) -> dict:
    if not file.filename:
        raise HTTPException(400, "no filename")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"unsupported extension {ext!r}; allowed: {sorted(ALLOWED_EXTS)}")

    video_id = db.new_id()
    dest = upload_path(video_id, ext)
    size = 0
    async with aiofiles.open(dest, "wb") as out:
        while chunk := await file.read(UPLOAD_CHUNK_BYTES):
            await out.write(chunk)
            size += len(chunk)

    db.create_video(file.filename, dest, size, video_id=video_id)
    return {"id": video_id, "filename": file.filename, "path": str(dest), "size": size}


@app.get("/api/videos")
def list_videos() -> list[dict]:
    return db.list_videos()


@app.get("/api/videos/{video_id}")
def get_video(video_id: str) -> dict:
    return _video_or_404(video_id)


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str) -> dict:
    v = _video_or_404(video_id)
    src = Path(v["path"])
    sidecar = pipeline.index_path_for(src)
    db.delete_video(video_id)
    src.unlink(missing_ok=True)
    sidecar.unlink(missing_ok=True)
    return {"deleted": video_id}


# ----- indexing -----

@app.post("/api/videos/{video_id}/index")
def start_index(video_id: str) -> dict:
    _video_or_404(video_id)
    job_id = jobs.submit_index_job(video_id)
    return {"job_id": job_id, "video_id": video_id, "status": "queued"}


@app.get("/api/videos/{video_id}/events")
def get_events(video_id: str) -> dict:
    v = _video_or_404(video_id)
    idx = pipeline.load_index(v["path"])
    if idx is None:
        raise HTTPException(404, "no index yet — run POST /api/videos/{id}/index first")
    return idx


# ----- search -----

@app.post("/api/videos/{video_id}/search")
def search_video(video_id: str, req: SearchRequest) -> dict:
    v = _video_or_404(video_id)
    idx = pipeline.load_index(v["path"])
    if idx is None:
        raise HTTPException(404, "video not indexed yet")
    if req.fanout:
        # Synchronous fanout — UX-wise the client should hit /api/jobs/find for long videos,
        # but for short ones this returns directly in a few seconds.
        hits = search_mod.find_fanout(idx, v["path"], req.query, limit=req.limit)
    else:
        hits = search_mod.text_search(idx, req.query, limit=req.limit)
    return {"hits": [asdict(h) for h in hits], "mode": "fanout" if req.fanout else "text"}


@app.post("/api/videos/{video_id}/search/async")
def search_video_async(video_id: str, req: SearchRequest) -> dict:
    """For slow fanout queries — enqueues as a job and returns a job_id."""
    _video_or_404(video_id)
    if not req.fanout:
        # Text search is fast; redirect callers to the sync endpoint.
        raise HTTPException(400, "use POST /search for non-fanout queries")
    job_id = jobs.submit_find_fanout_job(video_id, req.query)
    return {"job_id": job_id}


# ----- jobs -----

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    j = db.get_job(job_id)
    if j is None:
        raise HTTPException(404, f"job not found: {job_id}")
    return j


@app.get("/api/jobs")
def list_jobs(video_id: Optional[str] = None) -> list[dict]:
    return db.list_jobs(video_id=video_id)


# ----- clips -----

@app.post("/api/clips")
def create_clip(req: ClipRequest) -> dict:
    if req.end <= req.start:
        raise HTTPException(400, "end must be greater than start")
    v = _video_or_404(req.video_id)
    clip_id = db.new_id()
    out_path = clip_path(clip_id)
    clipper_mod.cut_clip(
        v["path"], req.start, req.end, out_path,
        stream_copy=not req.reencode,
    )
    size = out_path.stat().st_size
    db.create_clip(
        req.video_id, req.name, req.start, req.end, out_path, size, req.reencode,
        clip_id=clip_id,
    )
    return {
        "id": clip_id, "video_id": req.video_id, "name": req.name,
        "start": req.start, "end": req.end, "duration": req.end - req.start,
        "path": str(out_path), "size": size,
    }


@app.get("/api/clips")
def list_clips(video_id: Optional[str] = None) -> list[dict]:
    return db.list_clips(video_id=video_id)


@app.get("/api/clips/{clip_id}")
def get_clip(clip_id: str) -> dict:
    c = db.get_clip(clip_id)
    if c is None:
        raise HTTPException(404, f"clip not found: {clip_id}")
    return c


@app.delete("/api/clips/{clip_id}")
def delete_clip(clip_id: str) -> dict:
    c = db.get_clip(clip_id)
    if c is None:
        raise HTTPException(404, f"clip not found: {clip_id}")
    Path(c["path"]).unlink(missing_ok=True)
    db.delete_clip(clip_id)
    return {"deleted": clip_id}


# ----- media (byte-range playback) -----

@app.get("/media/videos/{video_id}")
def stream_video(video_id: str, request: Request) -> Response:
    v = _video_or_404(video_id)
    path = Path(v["path"])
    if not path.exists():
        raise HTTPException(404, "source file missing on disk")
    return _byte_range_response(path, request.headers.get("range"))


@app.get("/media/clips/{clip_id}")
def stream_clip(clip_id: str, request: Request) -> Response:
    c = db.get_clip(clip_id)
    if c is None:
        raise HTTPException(404, f"clip not found: {clip_id}")
    path = Path(c["path"])
    if not path.exists():
        raise HTTPException(404, "clip file missing on disk")
    return _byte_range_response(path, request.headers.get("range"))
