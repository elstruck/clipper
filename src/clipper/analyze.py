"""LLM-driven clip suggestions over an indexed video.

Two backend choices, auto-selected by environment:

1. OpenAI-compatible endpoint (preferred when set) — point `OPENAI_BASE_URL`
   at any compatible server (vLLM, Ollama, LM Studio, llama.cpp server, or
   real OpenAI). Set `MODEL_NAME` to the model alias the server uses
   (defaults to "llm" since that's what vLLM exposes by default).
   `OPENAI_API_KEY` is optional and defaults to a placeholder for servers
   that don't require auth.

2. Anthropic Claude (fallback) — set `ANTHROPIC_API_KEY`. Uses prompt
   caching automatically so re-runs against the same video are cheap.

Force a specific provider with `CLIPPER_LLM_PROVIDER=openai|anthropic`.

Both paths return the same `{prompt, model, generated_at, usage,
suggestions: [...]}` payload.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

DEFAULT_ANTHROPIC_MODEL = os.environ.get("CLIPPER_ANALYZE_MODEL", "claude-sonnet-4-6")
DEFAULT_OPENAI_MODEL = os.environ.get("MODEL_NAME", "llm")

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


Provider = str  # "openai" | "anthropic"


@dataclass(slots=True)
class Suggestion:
    start: float
    end: float
    title: str
    why: str


def _resolve_provider() -> Optional[Provider]:
    """Pick the active backend. Returns None when neither is configured."""
    forced = os.environ.get("CLIPPER_LLM_PROVIDER", "").strip().lower()
    if forced == "openai":
        return "openai" if os.environ.get("OPENAI_BASE_URL") else None
    if forced == "anthropic":
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else None
    # auto
    if os.environ.get("OPENAI_BASE_URL"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def is_configured() -> bool:
    return _resolve_provider() is not None


def active_provider() -> Optional[dict]:
    """Return a dict describing the active provider (or None)."""
    p = _resolve_provider()
    if p == "openai":
        return {
            "provider": "openai",
            "base_url": os.environ.get("OPENAI_BASE_URL"),
            "model": DEFAULT_OPENAI_MODEL,
        }
    if p == "anthropic":
        return {
            "provider": "anthropic",
            "base_url": None,
            "model": DEFAULT_ANTHROPIC_MODEL,
        }
    return None


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
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        if text.endswith("```"):
            text = text[:-3]
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


def _call_anthropic(context: str, user_prompt: str, model: str) -> tuple[str, dict, float]:
    import anthropic

    client = anthropic.Anthropic()
    t0 = time.time()
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[
            {"type": "text", "text": _SYSTEM},
            {
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
    usage = getattr(resp, "usage", None)
    usage_dict = {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
    } if usage else {}
    return raw_text, usage_dict, elapsed


def _call_openai_compatible(context: str, user_prompt: str, model: str) -> tuple[str, dict, float]:
    """Works against any OpenAI-compatible /v1 endpoint: vLLM, Ollama,
    LM Studio, llama.cpp server, or real OpenAI."""
    from openai import OpenAI

    base_url = os.environ.get("OPENAI_BASE_URL")
    # Most local setups don't require auth; placeholder keeps the SDK happy.
    api_key = os.environ.get("OPENAI_API_KEY", "EMPTY")

    client = OpenAI(base_url=base_url, api_key=api_key)

    # Put the static, large content in the SYSTEM message so prefix caching
    # (built into vLLM and OpenAI alike) kicks in on re-runs against the
    # same video. User message is short and changes.
    system_with_context = (
        f"{_SYSTEM}\n\n"
        f"Here is the video data you're analyzing:\n\n{context}"
    )

    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system_with_context},
                {"role": "user", "content": user_prompt},
            ],
            # Most vLLM deployments support JSON mode; if the server rejects
            # it we'll catch and retry below.
            response_format={"type": "json_object"},
            temperature=0.3,
        )
    except Exception as e:
        msg = str(e).lower()
        if "response_format" in msg or "json_object" in msg or "not supported" in msg:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_with_context},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
        else:
            raise
    elapsed = time.time() - t0

    raw_text = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    usage_dict: dict[str, Any] = {}
    if usage:
        usage_dict["input_tokens"] = getattr(usage, "prompt_tokens", None)
        usage_dict["output_tokens"] = getattr(usage, "completion_tokens", None)
        # OpenAI's usage details for cached tokens, if surfaced by the server.
        details = getattr(usage, "prompt_tokens_details", None)
        if details:
            cached = getattr(details, "cached_tokens", None)
            if cached:
                usage_dict["cache_read_input_tokens"] = cached
    return raw_text, usage_dict, elapsed


def analyze(idx: dict, user_prompt: str) -> dict:
    """Run one analysis pass. Returns the payload ready for sidecar storage."""
    provider = _resolve_provider()
    if provider is None:
        raise RuntimeError(
            "No LLM provider configured — set OPENAI_BASE_URL + MODEL_NAME "
            "(for a local model) or ANTHROPIC_API_KEY, then restart the server."
        )
    if not user_prompt.strip():
        raise ValueError("analysis prompt is empty")

    context = _format_index_for_llm(idx)

    if provider == "openai":
        model = DEFAULT_OPENAI_MODEL
        raw_text, usage, elapsed = _call_openai_compatible(context, user_prompt, model)
    else:
        model = DEFAULT_ANTHROPIC_MODEL
        raw_text, usage, elapsed = _call_anthropic(context, user_prompt, model)

    try:
        parsed = json.loads(_strip_json_envelope(raw_text))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"model did not return valid JSON: {e}\n--- raw output:\n{raw_text[:1500]}")

    duration = float(idx.get("duration") or 0.0)
    suggestions = _validate_suggestions(parsed, duration)

    return {
        "prompt": user_prompt,
        "provider": provider,
        "model": model,
        "generated_at": time.time(),
        "elapsed_seconds": round(elapsed, 2),
        "usage": usage,
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
