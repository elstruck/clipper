"""faster-whisper wrapper for speech transcription.

Lazy-loaded model singleton (same pattern as Marlin) running distil-large-v3
on CUDA in float16. About 2 GB VRAM, ~30× realtime on a 3090. English-only
by default; override via the `CLIPPER_WHISPER_MODEL` env var (e.g.
`large-v3` for multilingual).

`transcribe()` returns a dict shaped like:

    {
      "language": "en",
      "language_probability": 0.99,
      "duration": 1234.5,
      "segments": [
        {"start": 0.0, "end": 3.2, "text": "...", "no_speech_prob": 0.01},
        ...
      ]
    }

faster-whisper reads its audio via ffmpeg, so we pass video files directly —
no separate audio extraction needed.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Iterator, Optional

WHISPER_MODEL = os.environ.get("CLIPPER_WHISPER_MODEL", "distil-large-v3")

_model_cache: Any = None


def get_model() -> Any:
    """Lazy-load the Whisper model on CUDA. Cached for the process lifetime."""
    global _model_cache
    if _model_cache is None:
        from faster_whisper import WhisperModel
        _model_cache = WhisperModel(
            WHISPER_MODEL,
            device="cuda",
            compute_type="float16",
        )
    return _model_cache


def transcribe(
    video_path: str,
    *,
    language: Optional[str] = None,
    progress: Optional[Callable[[float, float], None]] = None,
) -> dict:
    """Transcribe `video_path` and return a dict with language + segments.

    The `progress(current_sec, total_sec)` callback fires once per yielded
    segment so callers can update job state during long transcriptions.
    """
    model = get_model()
    segments_iter, info = model.transcribe(
        video_path,
        language=language,           # None → auto-detect
        beam_size=5,
        vad_filter=True,             # skip silence — reduces hallucination
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=False,       # turn on later if we want word-level
        condition_on_previous_text=False,  # less hallucination on long media
    )

    duration = info.duration
    out_segments: list[dict] = []
    for seg in segments_iter:
        out_segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "no_speech_prob": round(seg.no_speech_prob, 3),
        })
        if progress:
            progress(seg.end, duration)

    return {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": round(duration, 3),
        "segments": out_segments,
    }
