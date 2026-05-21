"""FastAPI app — upload, index, search, cut, list.

Single-worker by design (the model is GPU-bound). Long-running work goes
through `jobs.py`; HTTP endpoints just enqueue and return immediately.

Run with: `clip serve` or `uv run uvicorn clipper.server:app --host 0.0.0.0 --port 8000`.
"""

from __future__ import annotations

# Import the package first so LD_LIBRARY_PATH + Marlin env vars are set
# before transformers/torch get loaded by any sub-import.
import clipper  # noqa: F401

import asyncio
import json
import logging
import mimetypes
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator, Optional

import time

import aiofiles
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clipper import analyze, clipper as clipper_mod
from clipper import db, jobs, pipeline, thumbnails
from clipper import search as search_mod
from clipper.auth import TokenAuthMiddleware, _configured_tokens
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


app = FastAPI(title="Marlin Clipper", version="0.5.0", lifespan=lifespan)
app.add_middleware(TokenAuthMiddleware)


# ---------------- models ----------------

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    fanout: bool = False
    limit: int = 20


class InitUploadRequest(BaseModel):
    filename: str
    size: int = Field(..., gt=0)


CHUNK_SIZE_HINT = 8 << 20  # 8 MiB — what the client should aim for per PUT


class ClipRequest(BaseModel):
    video_id: str
    start: float = Field(..., ge=0)
    end: float
    name: Optional[str] = None
    reencode: bool = False


class AnalyzeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)


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
    return {"status": "ok", "auth_required": bool(_configured_tokens())}


@app.get("/api/stats")
def stats() -> dict:
    """Aggregate disk usage + counts for the library header."""
    def _dir_size(p: Path) -> int:
        if not p.exists():
            return 0
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    uploads = _dir_size(UPLOADS_DIR)
    clips = _dir_size(CLIPS_DIR)
    thumbs = _dir_size(thumbnails.THUMBS_DIR)
    videos_count = len(db.list_videos())
    clips_count = len(db.list_clips())
    return {
        "uploads_bytes": uploads,
        "clips_bytes": clips,
        "thumbs_bytes": thumbs,
        "total_bytes": uploads + clips + thumbs,
        "videos_count": videos_count,
        "clips_count": clips_count,
        "auth_enabled": bool(_configured_tokens()),
    }


# ----- videos -----

# ----- chunked / resumable uploads -----

@app.post("/api/uploads")
def init_upload(req: InitUploadRequest) -> dict:
    """Start a resumable upload. Returns an upload_id + chunk size hint."""
    ext = Path(req.filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"unsupported extension {ext!r}; allowed: {sorted(ALLOWED_EXTS)}")
    # Place the file at a temp path keyed off the upload id; finalize will
    # rename it to the canonical <video_id>.<ext> path.
    upload_id = db.new_id()
    dest = UPLOADS_DIR / f"_upload_{upload_id}.{ext.lstrip('.')}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.touch(exist_ok=False)
    db.create_upload(req.filename, ext, dest, req.size, upload_id=upload_id)
    return {
        "upload_id": upload_id,
        "chunk_size": CHUNK_SIZE_HINT,
        "bytes_received": 0,
        "total_size": req.size,
    }


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str) -> dict:
    u = db.get_upload(upload_id)
    if u is None:
        raise HTTPException(404, "upload not found")
    return u


@app.put("/api/uploads/{upload_id}")
async def upload_chunk(
    upload_id: str,
    request: Request,
    offset: int = Query(..., ge=0),
) -> dict:
    """Append the request body to the upload file at the given offset.
    409 if offset doesn't match current bytes_received (client must call
    GET to resync). 410 if the upload was already finalized or aborted."""
    u = db.get_upload(upload_id)
    if u is None:
        raise HTTPException(404, "upload not found")
    if u["status"] != "open":
        raise HTTPException(410, f"upload status is {u['status']}")
    if offset != u["bytes_received"]:
        raise HTTPException(
            409,
            f"offset mismatch — server has {u['bytes_received']}, client sent {offset}",
        )

    path = Path(u["path"])
    received = u["bytes_received"]
    limit = u["total_size"]

    async with aiofiles.open(path, "ab") as f:
        async for chunk in request.stream():
            if not chunk:
                continue
            if received + len(chunk) > limit:
                # Trim overshoot defensively.
                chunk = chunk[: limit - received]
            await f.write(chunk)
            received += len(chunk)
            if received >= limit:
                break

    db.update_upload(upload_id, bytes_received=received, last_chunk_at=time.time())
    return {"bytes_received": received, "total_size": limit}


@app.post("/api/uploads/{upload_id}/finalize")
def finalize_upload(upload_id: str) -> dict:
    """Mark the upload complete and create the matching video row."""
    u = db.get_upload(upload_id)
    if u is None:
        raise HTTPException(404, "upload not found")
    if u["status"] != "open":
        raise HTTPException(410, f"upload status is {u['status']}")
    if u["bytes_received"] != u["total_size"]:
        raise HTTPException(
            400,
            f"incomplete: have {u['bytes_received']} of {u['total_size']} bytes",
        )

    # Adopt the upload row as a video. Reuse the same on-disk path.
    video_id = db.new_id()
    new_path = upload_path(video_id, u["ext"])
    Path(u["path"]).rename(new_path)
    db.create_video(u["filename"], new_path, u["total_size"], video_id=video_id)
    db.update_upload(upload_id, status="finalized")
    return {
        "id": video_id,
        "filename": u["filename"],
        "path": str(new_path),
        "size": u["total_size"],
    }


@app.delete("/api/uploads/{upload_id}")
def abort_upload(upload_id: str) -> dict:
    u = db.get_upload(upload_id)
    if u is None:
        raise HTTPException(404, "upload not found")
    Path(u["path"]).unlink(missing_ok=True)
    db.update_upload(upload_id, status="aborted")
    return {"aborted": upload_id}


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
    analyze.suggestions_path_for(src).unlink(missing_ok=True)
    thumbnails.sprite_path(video_id).unlink(missing_ok=True)
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


# ----- AI clip suggestions -----

@app.get("/api/analyze/config")
def analyze_config() -> dict:
    active = analyze.active_provider()
    return {
        "enabled": active is not None,
        "active": active,
        "default_prompt": analyze.DEFAULT_USER_PROMPT,
    }


@app.get("/api/videos/{video_id}/suggestions")
def get_suggestions(video_id: str) -> dict:
    v = _video_or_404(video_id)
    payload = analyze.load(v["path"])
    if payload is None:
        return {
            "prompt": analyze.DEFAULT_USER_PROMPT,
            "suggestions": [],
            "generated_at": None,
            "model": None,
        }
    return payload


@app.post("/api/videos/{video_id}/suggestions")
def regenerate_suggestions(video_id: str, req: AnalyzeRequest) -> dict:
    v = _video_or_404(video_id)
    idx = pipeline.load_index(v["path"])
    if idx is None:
        raise HTTPException(409, "video must be indexed first")
    try:
        payload = analyze.analyze(idx, req.prompt)
    except RuntimeError as e:
        # ANTHROPIC_API_KEY missing or model returned junk — surface to the UI.
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("analysis failed for %s", video_id)
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    analyze.save(v["path"], payload)
    return payload


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


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream of job state changes; closes when job ends."""

    async def stream() -> AsyncIterator[bytes]:
        last_state: Optional[tuple] = None
        # initial heartbeat so the browser knows the connection is live
        yield b": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            j = db.get_job(job_id)
            if j is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'job not found'})}\n\n".encode()
                return
            state = (j["status"], j["progress_current"], j["progress_total"], j["message"])
            if state != last_state:
                yield f"data: {json.dumps(j)}\n\n".encode()
                last_state = state
            if j["status"] in ("done", "failed"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@app.get("/api/videos/{video_id}/thumbnails")
def get_thumbnails(video_id: str) -> dict:
    """Lazily generate the thumbnail sprite and return its metadata + URL."""
    v = _video_or_404(video_id)
    spec = thumbnails.generate_sprite(video_id, v["path"], v.get("duration"))
    return {
        "url": f"/media/thumbnails/{video_id}",
        "count": spec.count,
        "interval": spec.interval,
        "width": spec.width,
        "height": spec.height,
        "thumb_width": thumbnails.THUMB_W,
        "thumb_height": thumbnails.THUMB_H,
    }


@app.get("/media/thumbnails/{video_id}")
def serve_thumbnails(video_id: str) -> Response:
    path = thumbnails.sprite_path(video_id)
    if not path.exists():
        raise HTTPException(404, "thumbnails not generated — POST /api/videos/{id}/thumbnails first")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/media/clips/{clip_id}")
def stream_clip(clip_id: str, request: Request) -> Response:
    c = db.get_clip(clip_id)
    if c is None:
        raise HTTPException(404, f"clip not found: {clip_id}")
    path = Path(c["path"])
    if not path.exists():
        raise HTTPException(404, "clip file missing on disk")
    return _byte_range_response(path, request.headers.get("range"))


# ---------------- frontend (production build) ----------------
# Mounted last so /api and /media routes win. Falls back to index.html
# for client-side React Router paths.

_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

if _WEB_DIST.exists():
    @app.get("/")
    def _serve_index() -> FileResponse:
        return FileResponse(_WEB_DIST / "index.html")

    # Catch-all for client-side routes (must come AFTER the API routes).
    @app.get("/videos/{video_id}")
    def _serve_video_route(video_id: str) -> FileResponse:  # noqa: ARG001
        return FileResponse(_WEB_DIST / "index.html")

    # Static asset mount for /assets/*, /*.svg, etc.
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=False), name="frontend")
else:
    @app.get("/")
    def _no_frontend() -> dict:
        return {
            "status": "ok",
            "frontend": "not built — run `cd web && npm run build`",
            "docs": "/docs",
        }
