#!/usr/bin/env python3
"""Validate delivery subtitles and optionally export WebVTT.

Hard quality gates:
  - positive, monotonic, non-overlapping timestamps
  - no cue above the visible-character limit
  - no cue below the minimum display duration
  - no cue above the reading-speed limit
  - optional lexical-stream identity against the corrected source
  - optional screen for machine-packed, non-semantic cue boundaries

The boundary screen is deliberately a risk detector, not a claim that a
machine has understood every cue.  A clean screen still needs the manual
semantic review required by the production skill.
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

_LEXICAL_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_SEMANTIC_BOUNDARY_END_RE = re.compile(
    r"[。！？!?；;，,：:、.…](?:[”’\"'）)\]】》〉」』]*)\s*$"
)

# These thresholds identify the specific failure mode produced when an
# unpunctuated transcript is greedily packed to the display limit.  They are
# intentionally conservative and require both a high overall concentration
# and a repeated run before blocking promotion.
PACKING_RISK_MIN_CUES = 20
PACKING_RISK_MIN_COUNT = 8
PACKING_RISK_MIN_RATIO = 0.60
PACKING_RISK_MIN_RUN = 4


def parse_stamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    if not 0 <= int(minutes) < 60 or not 0 <= float(seconds) < 60:
        raise ValueError(f"非法时间戳：{value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def visible_len(value: str) -> int:
    value = re.sub(r"<[^>]+>", "", value)
    return len(_LEXICAL_CHAR_RE.findall(value))


def lexical_stream(cues: list[dict]) -> str:
    """Return spoken alphanumeric/CJK content without layout or punctuation."""
    text = "".join(cue["text"] for cue in cues)
    text = re.sub(r"<[^>]+>", "", text)
    return "".join(_LEXICAL_CHAR_RE.findall(text))


def compare_lexical_streams(source_cues: list[dict], candidate_cues: list[dict]) -> dict:
    """Check that semantic re-segmentation changed layout/punctuation only."""
    source = lexical_stream(source_cues)
    candidate = lexical_stream(candidate_cues)
    mismatch_at = None
    for i, (left, right) in enumerate(zip(source, candidate)):
        if left != right:
            mismatch_at = i
            break
    if mismatch_at is None and len(source) != len(candidate):
        mismatch_at = min(len(source), len(candidate))
    return {
        "matches": source == candidate,
        "source_chars": len(source),
        "candidate_chars": len(candidate),
        "mismatch_at": mismatch_at,
    }


def inspect_boundary_quality(
    cues: list[dict],
    *,
    max_chars: int = 20,
) -> dict:
    """Detect repeated near-limit cues that do not end at semantic punctuation.

    This catches a common deterministic splitter failure: when the source has
    little punctuation, token packing creates many almost equally long cues at
    arbitrary word boundaries.  The result can pass timing and length checks
    while remaining unpleasant or misleading to read.
    """
    near_limit_floor = max(1, max_chars - 2)
    packed_indexes: list[int] = []
    flags: list[bool] = []
    boundary_ended = 0
    for cue in cues:
        ends_at_boundary = bool(_SEMANTIC_BOUNDARY_END_RE.search(cue["text"]))
        boundary_ended += int(ends_at_boundary)
        packed = visible_len(cue["text"]) >= near_limit_floor and not ends_at_boundary
        flags.append(packed)
        if packed:
            packed_indexes.append(cue["index"])

    longest_run = 0
    current_run = 0
    for packed in flags:
        if packed:
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0

    cue_count = len(cues)
    packed_ratio = len(packed_indexes) / max(cue_count, 1)
    risk = (
        cue_count >= PACKING_RISK_MIN_CUES
        and len(packed_indexes) >= PACKING_RISK_MIN_COUNT
        and packed_ratio >= PACKING_RISK_MIN_RATIO
        and longest_run >= PACKING_RISK_MIN_RUN
    )
    return {
        "method": "candidate_shape",
        "risk": risk,
        "requires_semantic_review": risk,
        "reason_codes": ["NEAR_LIMIT_BOUNDARY_SATURATION"] if risk else [],
        "near_limit_floor": near_limit_floor,
        "packed_without_boundary": packed_indexes,
        "packed_without_boundary_count": len(packed_indexes),
        "packed_without_boundary_ratio": packed_ratio,
        "longest_packed_run": longest_run,
        "boundary_ended_ratio": boundary_ended / max(cue_count, 1),
    }


def merge_boundary_quality(provenance: dict | None, candidate_shape: dict | None) -> dict | None:
    """Combine splitter provenance with an independent candidate-shape screen."""
    if not provenance:
        return candidate_shape
    if not candidate_shape:
        return provenance
    merged = dict(provenance)
    merged["candidate_shape"] = candidate_shape
    merged["risk"] = bool(provenance.get("risk") or candidate_shape.get("risk"))
    merged["requires_semantic_review"] = merged["risk"]
    merged["reason_codes"] = list(
        dict.fromkeys(
            [
                *provenance.get("reason_codes", []),
                *candidate_shape.get("reason_codes", []),
            ]
        )
    )
    return merged


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


def write_report(
    cues: list[dict],
    findings: dict,
    path: Path,
    *,
    max_chars: int = 20,
    min_duration: float = 0.2,
    max_cps: float = 25.0,
    text_integrity: dict | None = None,
    boundary_quality: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    structural_failed = sum(len(items) for items in findings.values())
    text_failed = bool(text_integrity and not text_integrity["matches"])
    boundary_failed = bool(boundary_quality and boundary_quality["risk"])
    failed = structural_failed or text_failed or boundary_failed
    lines = [
        "# 字幕 QC",
        "",
        f"- 字幕条数：{len(cues)}",
        f"- 起始时间：{cues[0]['start_stamp']}",
        f"- 末尾时间：{cues[-1]['end_stamp']}",
        f"- 可见字符上限：{max_chars}",
        f"- 最短显示时长：{min_duration:g} 秒",
        f"- 阅读速度上限：{max_cps:g} 字符／秒",
        f"- 非正时长：{len(findings['invalid'])}",
        f"- 时间重叠：{len(findings['overlaps'])}",
        f"- 超过字数上限：{len(findings['long'])}",
        f"- 短于时长下限：{len(findings['short'])}",
        f"- 超过阅读速度：{len(findings['fast'])}",
    ]
    if text_integrity is not None:
        lines.extend(
            [
                f"- 正文字符流与精校源一致：{'是' if text_integrity['matches'] else '否'}",
                f"- 精校源／候选正文字符数：{text_integrity['source_chars']} / "
                f"{text_integrity['candidate_chars']}",
            ]
        )
    if boundary_quality is not None:
        lines.append(
            "- 自动断句可信度："
            f"{'需语义复核' if boundary_quality['risk'] else '未触发风险'}"
        )
        if boundary_quality.get("method") == "split_provenance":
            lines.extend(
                [
                    f"- 源文本断句标点密度：{boundary_quality['breaks_per_100']:.2f} / 100 字",
                    f"- 最长无标点跨度：{boundary_quality['longest_unpunctuated_run']} 字",
                    "- fallback 边界："
                    f"{boundary_quality['fallback_boundary_count']} "
                    f"({boundary_quality['fallback_boundary_ratio']:.1%})",
                    "- 接近上限的 fallback 边界："
                    f"{boundary_quality['near_limit_fallback_count']} "
                    f"({boundary_quality['near_limit_fallback_ratio']:.1%})",
                    "- 最长连续近上限 fallback："
                    f"{boundary_quality['longest_near_limit_fallback_run']} 条",
                    "- 断句风险代码："
                    f"{', '.join(boundary_quality['reason_codes']) or '无'}",
                ]
            )
            candidate_shape = boundary_quality.get("candidate_shape")
            if candidate_shape is not None:
                lines.append(
                    "- 候选形态最长连续机械边界："
                    f"{candidate_shape['longest_packed_run']} 条"
                )
        else:
            lines.extend(
                [
                    "- 接近字数上限且未落在语义标点："
                    f"{boundary_quality['packed_without_boundary_count']} "
                    f"({boundary_quality['packed_without_boundary_ratio']:.1%})",
                    f"- 最长连续机械边界：{boundary_quality['longest_packed_run']} 条",
                ]
            )
    lines.append(f"- 结构／时间机器 QC：{'未通过' if structural_failed else '通过'}")
    lines.extend(
        [
            f"- 结论：{'未通过' if failed else '通过（机器门）'}",
            "",
        ]
    )
    if structural_failed:
        for key, items in findings.items():
            if items:
                lines.append(f"- {key}={items[:30]}")
    if text_failed:
        lines.append(f"- lexical_stream_mismatch_at={text_integrity['mismatch_at']}")
    if boundary_failed:
        if boundary_quality.get("packed_without_boundary"):
            lines.append(
                "- packed_without_boundary="
                f"{boundary_quality['packed_without_boundary'][:30]}"
            )
        else:
            lines.append(
                "- segmentation_reason_codes="
                f"{boundary_quality.get('reason_codes', [])}"
            )
        lines.append("- 处理：不要晋升；按语义重断句并人工通读，再重跑机器门。")
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
    parser.add_argument(
        "--source-srt",
        type=Path,
        default=None,
        help="精校源 SRT；检查重断句前后正文字符流一致",
    )
    parser.add_argument(
        "--check-boundary-quality",
        action="store_true",
        help="筛查接近字数上限的连续机械断句；触发时阻止晋升",
    )
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
    text_integrity = None
    if args.source_srt:
        try:
            source_cues = parse_srt(args.source_srt.resolve())
        except ValueError as error:
            if args.report:
                write_parse_error_report(args.report.resolve(), error)
            print(f"source_parse_error={error}")
            if not args.warn_only:
                sys.exit(1)
            return
        text_integrity = compare_lexical_streams(source_cues, cues)
    boundary_quality = (
        inspect_boundary_quality(cues, max_chars=args.max_chars)
        if args.check_boundary_quality
        else None
    )
    if args.report:
        write_report(
            cues,
            findings,
            args.report.resolve(),
            max_chars=args.max_chars,
            min_duration=args.min_duration,
            max_cps=args.max_cps,
            text_integrity=text_integrity,
            boundary_quality=boundary_quality,
        )

    failed = (
        sum(len(items) for items in findings.values())
        + int(bool(text_integrity and not text_integrity["matches"]))
        + int(bool(boundary_quality and boundary_quality["risk"]))
    )
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
        f"short={len(findings['short'])} fast={len(findings['fast'])} "
        f"text_match={text_integrity['matches'] if text_integrity else 'not_checked'} "
        f"boundary_risk={boundary_quality['risk'] if boundary_quality else 'not_checked'}"
    )
    if failed and not args.warn_only:
        sys.exit(1)


if __name__ == "__main__":
    main()
