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
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.atomic_delivery import commit_prepared_files


BLOCK_RE = re.compile(
    r"(?s)^\s*(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n"
    r"(.+?)\s*$"
)


def parse_stamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    if not 0 <= int(minutes) < 60 or not 0 <= float(seconds) < 60:
        raise ValueError(f"非法时间戳：{value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def visible_len(value: str) -> int:
    value = re.sub(r"<[^>]+>", "", value)
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", value))


def parse_srt(path: Path) -> list[dict]:
    """Parse every non-empty SRT block, failing if any block is malformed."""
    content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block for block in re.split(r"\n[ \t]*\n+", content.strip()) if block.strip()]
    if not blocks:
        raise ValueError(f"SRT 为空：{path}")

    cues = []
    for block_number, block in enumerate(blocks, 1):
        match = BLOCK_RE.fullmatch(block)
        if not match:
            excerpt = " ".join(block.splitlines())[:120]
            raise ValueError(
                f"SRT 第 {block_number} 块无法完整解析（不会静默忽略）：{excerpt!r}"
            )
        text = "\n".join(
            line.strip() for line in match.group(4).splitlines() if line.strip()
        )
        if not text:
            raise ValueError(f"SRT 第 {block_number} 块没有字幕文本")
        cues.append(
            {
                "index": int(match.group(1)),
                "start_stamp": match.group(2).replace(".", ","),
                "end_stamp": match.group(3).replace(".", ","),
                "start": parse_stamp(match.group(2)),
                "end": parse_stamp(match.group(3)),
                "text": text,
            }
        )
    indexes = [cue["index"] for cue in cues]
    if indexes != list(range(1, len(cues) + 1)):
        raise ValueError(
            "SRT 序号必须从 1 连续递增："
            f"实际前 20 个为 {indexes[:20]}"
        )
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
    path.parent.mkdir(parents=True, exist_ok=True)
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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def write_parse_error_report(path: Path, error: Exception) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# 字幕 QC\n\n"
        "- 字幕条数：未知\n"
        "- 结论：未通过\n"
        f"- 解析错误：{error}\n",
        encoding="utf-8",
    )


def promote_srt(source: Path, destination: Path) -> None:
    """Atomically promote a reviewed candidate without modifying the source."""
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("promoted SRT destination must differ from candidate source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def validate_distinct_paths(paths: dict[str, Path | None]) -> dict[str, Path]:
    """Resolve artifact roles and reject aliases before any file is written."""
    resolved = {
        role: path.resolve()
        for role, path in paths.items()
        if path is not None
    }
    seen: dict[Path, str] = {}
    for role, path in resolved.items():
        if path in seen:
            raise ValueError(
                f"artifact paths must be distinct: {seen[path]} and {role} both use {path}"
            )
        seen[path] = role
    return resolved


def _temporary_neighbor(path: Path, label: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.{label}.",
        dir=path.parent,
        delete=False,
    ) as handle:
        return Path(handle.name)


def promote_subtitle_pair(
    candidate_srt: Path,
    cues: list[dict],
    final_srt: Path,
    final_vtt: Path,
) -> None:
    """Promote a matching SRT/VTT pair, restoring both old files on failure."""
    paths = validate_distinct_paths(
        {
            "candidate_srt": candidate_srt,
            "final_srt": final_srt,
            "final_vtt": final_vtt,
        }
    )
    candidate_srt = paths["candidate_srt"]
    final_srt = paths["final_srt"]
    final_vtt = paths["final_vtt"]

    srt_tmp = _temporary_neighbor(final_srt, "candidate")
    vtt_tmp = _temporary_neighbor(final_vtt, "candidate")
    try:
        shutil.copy2(candidate_srt, srt_tmp)
        write_vtt(cues, vtt_tmp)
        commit_prepared_files(
            [(srt_tmp, final_srt), (vtt_tmp, final_vtt)]
        )
    finally:
        srt_tmp.unlink(missing_ok=True)
        vtt_tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="SRT/VTT 交付质量门")
    parser.add_argument("srt", type=Path)
    parser.add_argument("--write-vtt", type=Path, default=None)
    parser.add_argument(
        "--promote-srt",
        type=Path,
        default=None,
        help="QC 通过后把输入 candidate 原样晋升到此 final.srt 路径",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--max-chars", type=int, default=20)
    parser.add_argument("--min-duration", type=float, default=0.2)
    parser.add_argument("--max-cps", type=float, default=25.0)
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args()
    if args.promote_srt and not args.write_vtt:
        parser.error("--promote-srt 必须同时提供 --write-vtt，保证 final SRT/VTT 成对交付")
    if args.promote_srt and args.warn_only:
        parser.error("--promote-srt 不能与 --warn-only 同时使用")

    try:
        resolved = validate_distinct_paths(
            {
                "input_srt": args.srt,
                "final_srt": args.promote_srt,
                "final_vtt": args.write_vtt,
                "qc_report": args.report,
            }
        )
    except ValueError as error:
        parser.error(str(error))

    try:
        cues = parse_srt(resolved["input_srt"])
    except ValueError as error:
        if args.report:
            write_parse_error_report(args.report.resolve(), error)
        print(f"parse_error={error}")
        if not args.warn_only:
            sys.exit(1)
        return
    findings = inspect(
        cues,
        max_chars=args.max_chars,
        min_duration=args.min_duration,
        max_cps=args.max_cps,
    )
    if args.report:
        write_report(cues, findings, args.report.resolve())

    failed = sum(len(items) for items in findings.values())
    if args.promote_srt and not failed:
        promote_subtitle_pair(
            resolved["input_srt"],
            cues,
            resolved["final_srt"],
            resolved["final_vtt"],
        )
    elif args.write_vtt and not failed:
        write_vtt(cues, resolved["final_vtt"])
    print(
        f"cues={len(cues)} invalid={len(findings['invalid'])} "
        f"overlaps={len(findings['overlaps'])} long={len(findings['long'])} "
        f"short={len(findings['short'])} fast={len(findings['fast'])}"
    )
    if failed and not args.warn_only:
        sys.exit(1)


if __name__ == "__main__":
    main()
