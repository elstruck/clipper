# Marlin Clipper

Local web tool to find and clip moments in long videos using
[NemoStation/Marlin-2B](https://huggingface.co/NemoStation/Marlin-2B)
(a 2B-parameter video VLM) plus ffmpeg. Built for 30–120 minute recordings,
designed to be shareable with non-technical teammates.

## Status

**Phase 6** — Dev UX + live progress + fanout scoring. One-command
dev start (`uv run clip dev` or `cd web && npm run dev` — both bring
up the FastAPI server and Vite dev server together and open the
browser). Indexing progress streams over Server-Sent Events instead
of polling. Thumbnails are pre-warmed during indexing so the clip
editor opens instantly. Fanout searches now return a confidence
score (penalizes spans covering whole chunks), with a UI slider to
filter and a per-hit dismiss button.

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

## Sharing with your team

### Local: bind to the LAN

```bash
# Choose a long random token (this is the password your teammates will paste once)
export CLIPPER_TOKEN=$(openssl rand -hex 24)

# Bind to all interfaces so the LAN can reach it
uv run clip serve --host 0.0.0.0 --port 8000
```

Find the host's LAN IP (`ip -4 addr` on Linux, `ipconfig` on Windows),
hand teammates `http://<that-ip>:8000` and the token. The frontend
shows a sign-in screen on first visit and remembers the token in
`localStorage` thereafter.

To rotate the token, change `CLIPPER_TOKEN`, restart the server, and
re-share. Teammates will be prompted to re-enter.

### Off-LAN: Tailscale or Cloudflare Tunnel

`clip serve` is just a uvicorn process — anything that gives you a
URL to a port works. The two easy options:

- **Tailscale** — install on the host and on each teammate's device,
  share via the tailnet. URL becomes `http://<host-tailnet-name>:8000`.
  No port forwarding; encrypted by default.
- **Cloudflare Tunnel** — `cloudflared tunnel --url http://localhost:8000`
  hands you a `https://*.trycloudflare.com` URL. Public, so keep
  `CLIPPER_TOKEN` set.

### WSL2 caveat

WSL2 by default exposes ports only on `localhost`. To make the
server reachable from other devices on your LAN, run
`netsh interface portproxy add v4tov4 listenport=8000 connectport=8000 connectaddress=$(wsl hostname -I)`
from a Windows admin shell. Or run inside WSL2 with `--host 0.0.0.0`
and use the Windows host's IP — Windows forwards to WSL2 transparently
for the loopback adapter only.

## Web UI

### Quickest start — one command

```bash
uv run clip dev          # equivalent to: cd web && npm run dev
```

Both forms start the FastAPI API on `127.0.0.1:8765`, start the Vite
dev server on `127.0.0.1:5173`, and open the browser. Ctrl-C kills
both. First run also auto-installs `web/node_modules` if missing.

### Production-style (single port)

```bash
cd web && npm install && npm run build && cd ..
uv run clip serve
```

The built frontend is served by FastAPI from `web/dist`. Default
port is 8000; pass `--port` to change.

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
| `GET`  | `/api/jobs/{id}/events` | Server-Sent Events stream of state changes; closes when job ends |
| `GET`  | `/api/jobs` | List recent jobs |
| `POST` | `/api/clips` | Cut a clip from `{video_id, start, end}` |
| `GET`  | `/api/clips` | List clips |
| `GET`  | `/api/videos/{id}/thumbnails` | Lazily generate + return sprite metadata |
| `GET`  | `/media/thumbnails/{id}` | Horizontal JPEG sprite (cached, 12800×90 max) |
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
├── thumbnails.py  # ffmpeg sprite generation + caching for the timeline editor
├── server.py      # FastAPI app (+ static mount for web/dist)
└── __main__.py    # typer CLI (`clip` script)

web/
├── src/
│   ├── api.ts            # typed fetch wrappers for the FastAPI routes
│   ├── App.tsx           # root layout w/ react-router outlet
│   ├── main.tsx          # Vite entry, router config
│   ├── index.css         # dark theme tokens + components
│   ├── components/
│   │   └── ClipEditor.tsx  # canvas timeline + handles + looping preview
│   └── routes/
│       ├── Library.tsx   # uploads + grid of videos w/ status polling
│       └── Video.tsx     # player + events list + search + clips + editor
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
