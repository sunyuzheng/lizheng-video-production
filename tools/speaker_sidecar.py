"""Validate speaker-labeled subtitles against the current transcript."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from tools.subtitle_qc import parse_srt


_SPEAKER_PREFIX = re.compile(r"^[^\n：:]{1,40}[：:]\s*")


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def load_validated_speaker_srt(
    source_srt: Path,
    candidates: Iterable[Path],
) -> str:
    """Return a sidecar only when its cue timeline and words match source_srt."""
    if source_srt.suffix.lower() != ".srt":
        return ""
    source_cues = parse_srt(source_srt)
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not candidate.is_file():
            continue
        seen.add(resolved)
        try:
            labeled_cues = parse_srt(candidate)
        except ValueError:
            continue
        if len(labeled_cues) != len(source_cues):
            continue
        prefix_count = 0
        matches = True
        for source, labeled in zip(source_cues, labeled_cues):
            if (
                abs(source["start"] - labeled["start"]) > 0.005
                or abs(source["end"] - labeled["end"]) > 0.005
            ):
                matches = False
                break
            stripped, count = _SPEAKER_PREFIX.subn("", labeled["text"], count=1)
            prefix_count += count
            if _normalized_text(stripped) != _normalized_text(source["text"]):
                matches = False
                break
        if matches and prefix_count:
            return candidate.read_text(encoding="utf-8-sig")
    return ""
