# Marlin Clipper

Local web tool to find and clip moments in long videos using
[NemoStation/Marlin-2B](https://huggingface.co/NemoStation/Marlin-2B)
(a 2B-parameter video VLM) plus ffmpeg. Built for 30–120 minute recordings,
designed to be shareable with non-technical teammates.

## Status

**Phase 0** — environment + smoke test. Model loads on GPU, produces accurate
timestamped captions on a synthetic clip. No web UI or pipeline yet.

See the project plan for the remaining phases (long-video chunking, FastAPI
backend, web UI, timeline clip editor).

## Requirements

- Linux (tested on WSL2 Ubuntu)
- NVIDIA GPU with BF16 support (tested: RTX 3090, 24GB)
- NVIDIA driver new enough for CUDA 13 (we ship `torch==2.12.0+cu130`)
- ~10 GB disk for the model weights + dependencies
- A Hugging Face account with access to the
  [Marlin-2B](https://huggingface.co/NemoStation/Marlin-2B) gated repo

## One-time setup

### 1. Install host tools

```bash
# uv (Python project manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# ffmpeg ≥ 7 (we use conda — torchcodec needs FFmpeg 7 or 8 shared libs)
conda install -y -c conda-forge "ffmpeg>=7"
```

### 2. Sync Python deps

```bash
cd /home/elstruck/sites/video-processing
PATH="$HOME/.local/bin:$PATH" uv sync
```

### 3. Authenticate with Hugging Face

Accept the gate at <https://huggingface.co/NemoStation/Marlin-2B> while signed in,
then create a read token at <https://huggingface.co/settings/tokens> and:

```bash
uv run hf auth login   # paste token when prompted
```

## Verify the install (smoke test)

```bash
uv run python scripts/gen_test_clip.py   # makes a 20s synthetic clip
uv run python scripts/smoke_test.py      # downloads model on first run (~4–5 GB)
```

Expected output ends with `== OK: caption <Ns>, find <Ns> ==`. The smoke test:

- Loads Marlin-2B in BF16 on CUDA
- Runs `caption()` and prints scene + timestamped events
- Runs `find()` with a sample query and prints the resolved time span
- Writes `scripts/_test_clip.events.json`

### About `LD_LIBRARY_PATH`

`torchcodec` needs ffmpeg's shared libs (`libavutil`, `libavcodec`, ...) at
dlopen time. The smoke test auto-prepends `/home/elstruck/miniconda3/lib`
(where conda installs them) by re-execing Python with the env var set —
glibc only reads `LD_LIBRARY_PATH` at process start. Override the path via
`VIDPROC_FFMPEG_LIB=/some/other/dir` if needed.

## Repo layout

```
.
├── pyproject.toml           # uv-managed project, deps pinned to CUDA-13 torch wheels
├── scripts/
│   ├── gen_test_clip.py     # ffmpeg-generated 20s test video
│   └── smoke_test.py        # Phase 0 verification
└── README.md
```

Later phases will add `src/clipper/` (pipeline modules), `web/` (React frontend),
and `uploads/` + `clips/` directories (gitignored).

## License

Apache-2.0 (matches Marlin-2B).
