"""Marlin-2B client: lazy model loader + caption/find wrappers.

The wrappers take just a video path — Marlin's processor (Qwen3VL) doesn't
support time ranges in the message dict, so callers must pre-extract chunks
via `chunker.extract_chunk` if they want to process sub-ranges of a longer
video.

The model's prompt strings and parser helpers live in the dynamically-loaded
`modeling_marlin.py` module; we re-export the canonical `caption()`/`find()`
methods directly to inherit any future tweaks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import torch
from transformers import AutoModelForCausalLM

MODEL_ID = "NemoStation/Marlin-2B"

_model_cache: Any = None


def get_model() -> Any:
    """Lazy-load Marlin-2B onto CUDA in BF16. Cached for the process lifetime."""
    global _model_cache
    if _model_cache is None:
        _model_cache = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            trust_remote_code=True,
            dtype=torch.bfloat16,
            device_map={"": "cuda"},
        )
    return _model_cache


@dataclass(slots=True)
class CaptionChunk:
    scene: str
    events: list[dict]  # {"start": float, "end": float, "description": str}
    raw: str


@dataclass(slots=True)
class FindHit:
    span: Optional[tuple[float, float]]  # local to the requested video
    raw: str
    format_ok: bool


def caption(model: Any, video_path: str) -> CaptionChunk:
    """Run dense captioning on a (possibly pre-chunked) video file."""
    result = model.caption(str(video_path))
    return CaptionChunk(
        scene=result.get("scene", ""),
        events=list(result.get("events", []) or []),
        raw=result.get("caption", ""),
    )


def find(model: Any, video_path: str, event: str) -> FindHit:
    """Run temporal grounding on a (possibly pre-chunked) video file."""
    event_str = (event or "").strip()
    if not event_str:
        raise ValueError("`event` must be non-empty")
    result = model.find(str(video_path), event=event_str)
    span = result.get("span")
    return FindHit(
        span=span,
        raw=result.get("raw", ""),
        format_ok=bool(result.get("format_ok")),
    )
