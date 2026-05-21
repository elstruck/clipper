"""LLM-driven clip suggestions over an indexed video.

Reads the index sidecar (Marlin captions + Whisper transcript), sends it
to Claude with a user-controllable prompt, parses out clip suggestions.

Uses prompt caching so re-runs with the same video but a tweaked prompt
only pay for the new prompt tokens. The video index goes into a cached
system block; the user's analysis prompt is the ephemeral user message.

Requires `ANTHROPIC_API_KEY` in the environment. We surface a clean
RuntimeError if it's missing so the UI can show a helpful message.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

# Default model — fast and capable for this kind of structured analysis.
DEFAULT_MODEL = os.environ.get("CLIPPER_ANALYZE_MODEL", "claude-sonnet-4-6")

DEFAULT_USER_PROMPT = """Identify 5–10 standout moments in this video that would make great standalone clips.

For each, give:
- start_time / end_time in seconds (aim for 10–60 second clips, longer if a single thought needs it)
- a short, punchy title (under 10 words)
- a one-sentence "why this is good" pulled from what's actually happening or said in that span

Prefer self-contained narrative beats — a complete thought, a clear demo step, a striking visual moment. Skip filler stretches. Order by start time.
""".strip()


_SYSTEM = """You are a video editor's assistant. You read a video's visual captions (per-second scene descriptions) plus its speech transcript, and identify the most compelling moments to extract as standalone clips.

Return ONLY a JSON object with this exact shape — no prose, no code fences, no commentary:

{"suggestions": [{"start": <float seconds>, "end": <float seconds>, "title": "<short title>", "why": "<one sentence>"}, ...]}

`start` and `end` must be real timestamps that appear in the transcript or caption ranges below. `end` > `start`."""


@dataclass(slots=True)
class Suggestion:
    start: float
    end: float
    title: str
    why: str


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _format_index_for_llm(idx: dict) -> str:
    duration = idx.get("duration") or 0.0
    lines = [f"# Video duration: {duration:.1f}s", ""]

    events = idx.get("events") or []
    if events:
        lines.append("## Visual captions (Marlin-2B, per-event)")
        for ev in events:
            lines.append(f"[{ev['start']:.1f}-{ev['end']:.1f}] {ev['description']}")
        lines.append("")

    transcript = idx.get("transcript") or {}
    segs = transcript.get("segments") or []
    if segs:
        lang = transcript.get("language", "?")
        lines.append(f"## Speech transcript ({lang})")
        for s in segs:
            text = s["text"].strip().replace("\n", " ")
            lines.append(f"[{s['start']:.1f}-{s['end']:.1f}] {text}")
        lines.append("")

    return "\n".join(lines)


def _strip_json_envelope(text: str) -> str:
    """Tolerate code fences or stray prose, extract the JSON object."""
    text = text.strip()
    if text.startswith("```"):
        # ```json\n ... \n```
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        if text.endswith("```"):
            text = text[:-3]
    # If the model added prose, grab the first {...} block.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else text


def _validate_suggestions(payload: Any, duration: float) -> list[Suggestion]:
    if not isinstance(payload, dict):
        raise ValueError(f"expected object, got {type(payload).__name__}")
    items = payload.get("suggestions")
    if not isinstance(items, list):
        raise ValueError("missing or non-list 'suggestions'")
    out: list[Suggestion] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        start = max(0.0, min(start, duration))
        end = max(start + 0.05, min(end, duration))
        out.append(Suggestion(
            start=round(start, 3),
            end=round(end, 3),
            title=str(raw.get("title", "")).strip()[:200] or "(untitled)",
            why=str(raw.get("why", "")).strip()[:500],
        ))
    return out


def analyze(idx: dict, user_prompt: str, *, model: Optional[str] = None) -> dict:
    """Run a single analysis pass. Returns a dict ready for sidecar storage."""
    if not is_configured():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — export it in the environment to use the AI suggestions feature."
        )
    if not user_prompt.strip():
        raise ValueError("analysis prompt is empty")

    import anthropic

    client = anthropic.Anthropic()
    mdl = model or DEFAULT_MODEL

    context = _format_index_for_llm(idx)

    t0 = time.time()
    resp = client.messages.create(
        model=mdl,
        max_tokens=4096,
        system=[
            {"type": "text", "text": _SYSTEM},
            {
                # Big static blob — cache it. Re-runs against the same video pay
                # the cache-write cost once, then cache-read pricing thereafter.
                "type": "text",
                "text": "Here is the video data you're analyzing:\n\n" + context,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    elapsed = time.time() - t0

    text_blocks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    raw_text = "\n".join(text_blocks).strip()

    try:
        parsed = json.loads(_strip_json_envelope(raw_text))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"model did not return valid JSON: {e}\n--- raw output:\n{raw_text[:1500]}")

    duration = float(idx.get("duration") or 0.0)
    suggestions = _validate_suggestions(parsed, duration)

    usage = getattr(resp, "usage", None)
    usage_dict = {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
    } if usage else {}

    return {
        "prompt": user_prompt,
        "model": mdl,
        "generated_at": time.time(),
        "elapsed_seconds": round(elapsed, 2),
        "usage": usage_dict,
        "suggestions": [asdict(s) for s in suggestions],
    }


def suggestions_path_for(video_path: str | Path) -> Path:
    p = Path(video_path)
    return p.with_suffix(p.suffix + ".suggestions.json")


def load(video_path: str | Path) -> Optional[dict]:
    p = suggestions_path_for(video_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def save(video_path: str | Path, payload: dict) -> Path:
    p = suggestions_path_for(video_path)
    p.write_text(json.dumps(payload, indent=2))
    return p
