# Marlin Clipper

Local web tool to find and clip moments in long videos using
[NemoStation/Marlin-2B](https://huggingface.co/NemoStation/Marlin-2B)
(a 2B-parameter video VLM) plus ffmpeg. Built for 30–120 minute recordings,
designed to be shareable with non-technical teammates.

## Status

**Phase 3** — Web UI MVP. React + Vite frontend served by FastAPI on a
single port. Library page for uploads and indexing; video page with
player, scrollable events list (synced to the playhead), search panel
(text + find modes), and a clip list with download links. No
frame-accurate timeline editor yet — that's Phase 4.

## Requirements

- Linux (tested on WSL2 Ubuntu)
- NVIDIA GPU with BF16 support (tested: RTX 3090, 24GB)
- NVIDIA driver new enough for CUDA 13 (we ship `torch==2.12.0+cu130`)
- ffmpeg ≥ 7 (torchcodec needs it)
- ~10 GB disk for model weights + dependencies
- A Hugging Face account with access to the
  [Marlin-2B](https://huggingface.co/NemoStation/Marlin-2B) gated repo

## One-time setup

```bash
# 1. host tools
curl -LsSf https://astral.sh/uv/install.sh | sh
conda install -y -c conda-forge "ffmpeg>=7"

# 2. python deps
cd /home/elstruck/sites/video-processing
PATH="$HOME/.local/bin:$PATH" uv sync

# 3. HF auth (after accepting the gate at huggingface.co/NemoStation/Marlin-2B)
uv run hf auth login
```

## Verify the install (smoke test)

```bash
uv run python scripts/gen_test_clip.py
uv run python scripts/smoke_test.py        # downloads model on first run, ~4–5 GB
```

## CLI

```bash
uv run clip --help
```

Commands:

| Command | What it does |
|---|---|
| `clip index <video>` | Chunks the video, captions each chunk, writes `<video>.index.json` |
| `clip search <video> <query>` | Fast text search over cached caption events |
| `clip search <video> <query> --fanout` | Slower per-chunk `find()` for vocabulary-mismatch queries |
| `clip cut <video> <start> <end> -o <out>` | Cut a clip via ffmpeg (`-c copy` by default, `--reencode` for frame-accurate) |
| `clip show <video>` | Print the cached index summary |

### Example

```bash
# Generate a 4-minute synthetic test clip
uv run python scripts/gen_long_test_clip.py

# Index it (takes ~30s on a 3090, 3 chunks)
uv run clip index scripts/_long_test_clip.mp4

# Search for content
uv run clip search scripts/_long_test_clip.mp4 "color bars"
uv run clip search scripts/_long_test_clip.mp4 "fractal" --fanout

# Cut the fractal section
uv run clip cut scripts/_long_test_clip.mp4 120 180 -o /tmp/fractal.mp4
```

## Web UI

```bash
# 1. Build the frontend (one-time, or whenever web/src changes)
cd web && npm install && npm run build

# 2. Start the server — serves the built UI at /, API at /api/*, media at /media/*
uv run clip serve
```

Then open <http://127.0.0.1:8000> (or whatever host/port you bound to).

For frontend development with hot reload, run the API and the Vite dev server in two terminals:

```bash
# terminal 1
uv run clip serve --port 8765

# terminal 2
cd web && npm run dev   # http://127.0.0.1:5173, proxies /api + /media to :8765
```

## HTTP API

Start the server:

```bash
uv run clip serve              # default: 0.0.0.0:8000
uv run clip serve --port 8765  # custom port
```

Interactive docs are auto-generated at `/docs` (Swagger) and `/redoc`. Key routes:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/videos` | Multipart upload — returns `{id, ...}` |
| `GET`  | `/api/videos` | List all uploaded videos |
| `GET`  | `/api/videos/{id}` | Video metadata |
| `DELETE` | `/api/videos/{id}` | Delete video + index sidecar |
| `POST` | `/api/videos/{id}/index` | Enqueue indexing job — returns `{job_id}` |
| `GET`  | `/api/videos/{id}/events` | Cached timeline JSON |
| `POST` | `/api/videos/{id}/search` | Synchronous text or fanout search |
| `POST` | `/api/videos/{id}/search/async` | Enqueue fanout as a job (for long videos) |
| `GET`  | `/api/jobs/{id}` | Job status + progress + `result` JSON |
| `GET`  | `/api/jobs` | List recent jobs |
| `POST` | `/api/clips` | Cut a clip from `{video_id, start, end}` |
| `GET`  | `/api/clips` | List clips |
| `GET`  | `/media/videos/{id}` | Stream source with HTTP Range support |
| `GET`  | `/media/clips/{id}` | Stream rendered clip |

Storage layout (override with `CLIPPER_DATA_ROOT`):

```
data/
├── uploads/<video_id>.<ext>
├── clips/<clip_id>.mp4
└── clipper.db          # SQLite: videos, jobs, clips
```

Index JSON sidecars stay next to the source video as `<filename>.index.json`.

## Architecture

```
src/clipper/
├── _env.py        # LD_LIBRARY_PATH re-exec + qwen-vl-utils env vars
├── marlin.py      # lazy model singleton, caption/find wrappers
├── chunker.py     # ffprobe duration, chunk windowing, ffmpeg chunk extraction
├── pipeline.py    # index_video: extract → caption → merge → JSON
├── search.py      # text_search + find_fanout
├── clipper.py     # ffmpeg cut wrapper
├── storage.py     # filesystem layout (data root, uploads/, clips/, db)
├── db.py          # sqlite schema + DAO
├── jobs.py        # single-worker thread + progress tracking
├── server.py      # FastAPI app (+ static mount for web/dist)
└── __main__.py    # typer CLI (`clip` script)

web/
├── src/
│   ├── api.ts            # typed fetch wrappers for the FastAPI routes
│   ├── App.tsx           # root layout w/ react-router outlet
│   ├── main.tsx          # Vite entry, router config
│   ├── index.css         # dark theme tokens + components
│   └── routes/
│       ├── Library.tsx   # uploads + grid of videos w/ status polling
│       └── Video.tsx     # player + events list + search + clip list
├── vite.config.ts        # dev proxy of /api + /media to FastAPI
└── dist/                 # built bundle (served by FastAPI at /)
```

### Notes & gotchas

- Marlin's `caption()`/`find()` take a video path only — the Qwen3VL
  processor in transformers does **not** honor `video_start`/`video_end` keys
  in the message dict (those are qwen-vl-utils-only, and Marlin's processor
  is the transformers-native one). Chunks are therefore extracted to temp
  mp4s via ffmpeg `-c copy` (~1s for a 90s 360p chunk).
- Stream-copy extraction snaps to the nearest preceding keyframe, so chunk
  boundaries can drift by up to one keyframe-interval. Real videos with
  sparse keyframes should use `--reencode` for accuracy.
- `torchcodec` needs ffmpeg's shared libs at dlopen time. The package
  re-execs Python with `LD_LIBRARY_PATH` prepended to conda's lib dir on
  first import — glibc reads the linker path only at process start, so
  this trick is unavoidable. Override via `VIDPROC_FFMPEG_LIB=/some/path`.

## License

Apache-2.0 (matches Marlin-2B).
