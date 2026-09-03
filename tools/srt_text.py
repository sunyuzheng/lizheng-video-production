#!/usr/bin/env python3
"""Shared, strict SRT-to-text views for model-facing content steps."""

from __future__ import annotations

from pathlib import Path

from tools.subtitle_qc import parse_srt


def cues_before_timeline_reset(path: Path, reset_tolerance: float = 30.0) -> list[dict]:
    """Parse every block and stop before an appended highlight timeline reset."""
    cues = parse_srt(path)
    result: list[dict] = []
    previous_start: float | None = None
    for cue in cues:
        if (
            previous_start is not None
            and cue["start"] + reset_tolerance <= previous_start
        ):
            break
        result.append(cue)
        previous_start = cue["start"]
    return result


def plain_text_from_srt(path: Path, max_chars: int = 0) -> str:
    cues = cues_before_timeline_reset(path)
    text = " ".join(
        " ".join(cue["text"].splitlines()).strip() for cue in cues
    ).strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "…（已截断）"
    return text


def _format_window_timestamp(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}:{minutes:02d}:00"
    return f"{minutes:02d}:00"


def _format_cue_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def timed_text_from_srt(path: Path, window_seconds: int = 60) -> str:
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    buckets: dict[int, list[str]] = {}
    for cue in cues_before_timeline_reset(path):
        bucket_start = (int(cue["start"]) // window_seconds) * window_seconds
        text = " ".join(cue["text"].splitlines()).strip()
        if text:
            buckets.setdefault(bucket_start, []).append(text)
    return "\n".join(
        f"[{_format_window_timestamp(start)}] {' '.join(texts)}"
        for start, texts in sorted(buckets.items())
    )


def cue_timed_text_from_srt(path: Path) -> str:
    """Return one exact in/out range per cue for edit-decision prompts."""
    lines: list[str] = []
    for cue in cues_before_timeline_reset(path):
        text = " ".join(cue["text"].splitlines()).strip()
        if not text:
            continue
        lines.append(
            f"[{_format_cue_timestamp(cue['start'])} --> "
            f"{_format_cue_timestamp(cue['end'])}] {text}"
        )
    return "\n".join(lines)
