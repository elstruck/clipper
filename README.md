# Marlin Clipper

Local web tool to find and clip moments in long videos using
[NemoStation/Marlin-2B](https://huggingface.co/NemoStation/Marlin-2B)
(a 2B-parameter video VLM) plus ffmpeg. Built for 30–120 minute recordings,
designed to be shareable with non-technical teammates.

## Status

**Phase 1** — end-to-end CLI pipeline. Chunks long videos, captions each
chunk with Marlin, merges into a global timeline, supports text search +
per-chunk `find()` fanout, cuts clips via ffmpeg. No web UI yet.

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

## Architecture (Phase 1)

```
src/clipper/
├── _env.py        # LD_LIBRARY_PATH re-exec + qwen-vl-utils env vars
├── marlin.py      # lazy model singleton, caption/find wrappers
├── chunker.py     # ffprobe duration, chunk windowing, ffmpeg chunk extraction
├── pipeline.py    # index_video: extract → caption → merge → JSON
├── search.py      # text_search (token overlap) + find_fanout (per-chunk grounding)
├── clipper.py     # ffmpeg cut wrapper (stream copy or re-encode)
└── __main__.py    # typer CLI, registered as the `clip` script entry point
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
