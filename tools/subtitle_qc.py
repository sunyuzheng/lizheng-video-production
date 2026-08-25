#!/usr/bin/env python3
"""Validate delivery subtitles and optionally export WebVTT.

Hard quality gates:
  - positive, monotonic, non-overlapping timestamps
  - no cue above the visible-character limit
  - no cue below the minimum display duration
  - no cue above the reading-speed limit
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


BLOCK_RE = re.compile(
    r"(?ms)^\s*(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n"
    r"(.*?)(?=\n\s*\n|\Z)"
)


def parse_stamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def visible_len(value: str) -> int:
    value = re.sub(r"<[^>]+>", "", value)
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", value))


def parse_srt(path: Path) -> list[dict]:
    cues = []
    for match in BLOCK_RE.finditer(path.read_text(encoding="utf-8-sig")):
        cues.append(
            {
                "index": int(match.group(1)),
                "start_stamp": match.group(2).replace(".", ","),
                "end_stamp": match.group(3).replace(".", ","),
                "start": parse_stamp(match.group(2)),
                "end": parse_stamp(match.group(3)),
                "text": "\n".join(
                    line.strip() for line in match.group(4).splitlines() if line.strip()
                ),
            }
        )
    if not cues:
        raise ValueError(f"没有解析到 SRT cue：{path}")
    return cues


def inspect(
    cues: list[dict],
    *,
    max_chars: int = 20,
    min_duration: float = 0.2,
    max_cps: float = 25.0,
) -> dict:
    result = {"invalid": [], "overlaps": [], "long": [], "short": [], "fast": []}
    for i, cue in enumerate(cues):
        duration = cue["end"] - cue["start"]
        chars = visible_len(cue["text"])
        if duration <= 0:
            result["invalid"].append(cue["index"])
        if i and cue["start"] < cues[i - 1]["end"] - 0.001:
            result["overlaps"].append((cues[i - 1]["index"], cue["index"]))
        if chars > max_chars:
            result["long"].append((cue["index"], chars))
        if duration < min_duration - 0.001:
            result["short"].append((cue["index"], round(duration, 3)))
        if chars / max(duration, 0.001) > max_cps + 0.01:
            result["fast"].append((cue["index"], round(chars / max(duration, 0.001), 2)))
    return result


def write_vtt(cues: list[dict], path: Path) -> None:
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines.extend(
            [
                f"{cue['start_stamp'].replace(',', '.')} --> "
                f"{cue['end_stamp'].replace(',', '.')}",
                cue["text"],
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_report(cues: list[dict], findings: dict, path: Path) -> None:
    failed = sum(len(items) for items in findings.values())
    lines = [
        "# 字幕 QC",
        "",
        f"- 字幕条数：{len(cues)}",
        f"- 起始时间：{cues[0]['start_stamp']}",
        f"- 末尾时间：{cues[-1]['end_stamp']}",
        f"- 非正时长：{len(findings['invalid'])}",
        f"- 时间重叠：{len(findings['overlaps'])}",
        f"- 超过字数上限：{len(findings['long'])}",
        f"- 短于时长下限：{len(findings['short'])}",
        f"- 超过阅读速度：{len(findings['fast'])}",
        f"- 结论：{'通过' if failed == 0 else '未通过'}",
        "",
    ]
    if failed:
        for key, items in findings.items():
            if items:
                lines.append(f"- {key}={items[:30]}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="SRT/VTT 交付质量门")
    parser.add_argument("srt", type=Path)
    parser.add_argument("--write-vtt", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--max-chars", type=int, default=20)
    parser.add_argument("--min-duration", type=float, default=0.2)
    parser.add_argument("--max-cps", type=float, default=25.0)
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args()

    cues = parse_srt(args.srt.resolve())
    findings = inspect(
        cues,
        max_chars=args.max_chars,
        min_duration=args.min_duration,
        max_cps=args.max_cps,
    )
    if args.write_vtt:
        write_vtt(cues, args.write_vtt.resolve())
    if args.report:
        write_report(cues, findings, args.report.resolve())

    failed = sum(len(items) for items in findings.values())
    print(
        f"cues={len(cues)} invalid={len(findings['invalid'])} "
        f"overlaps={len(findings['overlaps'])} long={len(findings['long'])} "
        f"short={len(findings['short'])} fast={len(findings['fast'])}"
    )
    if failed and not args.warn_only:
        sys.exit(1)


if __name__ == "__main__":
    main()
