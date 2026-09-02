#!/usr/bin/env python3
"""Deterministic quality gates for model-generated publishing assets."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from tools.subtitle_qc import parse_srt


class AssetValidationError(RuntimeError):
    """A generated asset does not satisfy its delivery contract."""


_CHAPTER_RE = re.compile(
    r"^(?P<stamp>(?:\d{2,}:\d{2}|[1-9]\d*:\d{2}:\d{2}))\s+(?P<title>\S.*)$"
)
_OPENING_TYPE_RE = re.compile(
    r"^开头类型[：:]\s*(source-cold-open|host-narrative|hybrid)\s*$",
    re.MULTILINE,
)
_SOURCE_CLIP_RE = re.compile(
    r"^原片[：:]\s*"
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})\s*[｜|]\s*"
    r"(?P<quote>\S.*)$",
    re.MULTILINE,
)
_ENTRY_RE = re.compile(
    r"^进入正片[：:]\s*(?P<stamp>\d{2}:\d{2}:\d{2},\d{3}|待定位).*$",
    re.MULTILINE,
)
_HOST_SCRIPT_RE = re.compile(r"^补录逐字稿[：:]\s*(?P<script>\S.*)$", re.MULTILINE)


def _chapter_seconds(stamp: str) -> int:
    parts = [int(part) for part in stamp.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        if seconds >= 60:
            raise ValueError(f"秒数必须小于 60：{stamp}")
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = parts
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"时或分格式非法：{stamp}")
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"时间戳格式非法：{stamp}")


def _srt_stamp_seconds(stamp: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", stamp)
    if not match:
        raise ValueError(f"SRT 时间戳格式非法：{stamp}")
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"SRT 时间戳格式非法：{stamp}")
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _field_plain_text(section: str) -> str:
    return "\n".join(
        re.sub(r"^[-*]\s*", "", line.strip().replace("**", "").replace("__", ""))
        for line in section.splitlines()
        if line.strip()
    )


def _quote_key(value: str) -> str:
    return "".join(
        character
        for character in value
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "S"))
    ).casefold()


def validate_youtube_description(text: str, srt_path: Path) -> list[str]:
    """Return all chapter errors relative to the authoritative subtitle domain."""
    errors: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    marker_indexes = [
        index for index, line in enumerate(lines) if line.strip() in {"章节：", "章节:"}
    ]
    if len(marker_indexes) != 1:
        return [f"必须且只能有一行“章节：”，实际 {len(marker_indexes)} 行"]

    chapter_lines = [line.strip() for line in lines[marker_indexes[0] + 1 :] if line.strip()]
    if not chapter_lines:
        return ["“章节：”后没有章节"]

    chapters: list[tuple[str, int]] = []
    for line in chapter_lines:
        match = _CHAPTER_RE.fullmatch(line)
        if not match:
            errors.append(f"章节行格式非法：{line!r}")
            continue
        stamp = match.group("stamp")
        try:
            seconds = _chapter_seconds(stamp)
        except ValueError as error:
            errors.append(str(error))
            continue
        chapters.append((stamp, seconds))

    if errors:
        return errors
    if len(chapters) < 3:
        errors.append(f"YouTube 章节至少需要 3 个，实际 {len(chapters)} 个")
    if chapters and chapters[0][1] != 0:
        errors.append(f"第一个章节必须从 00:00 开始，实际 {chapters[0][0]}")

    cues = parse_srt(srt_path)
    end_seconds = cues[-1]["end"]
    for position, (stamp, seconds) in enumerate(chapters):
        if seconds > end_seconds + 0.001:
            errors.append(
                f"章节 {stamp} 超出字幕时域（末尾 {cues[-1]['end_stamp']}）"
            )
        if position:
            previous_stamp, previous_seconds = chapters[position - 1]
            if seconds <= previous_seconds:
                errors.append(f"章节时间戳未严格递增：{previous_stamp} → {stamp}")
            elif seconds - previous_seconds < 10:
                errors.append(
                    f"YouTube 章节间隔必须至少 10 秒：{previous_stamp} → {stamp}"
                )
    return errors


def validate_title_output(
    text: str,
    source_srt_path: Path | None = None,
    *,
    speaker_attribution_verified: bool = False,
) -> list[str]:
    """Validate the stable handoff structure promised by the title prompt."""
    required = ["## 首选组合", "## 备选组合", "## 放弃的方向"]
    positions = [text.find(header) for header in required]
    errors = [header + " 缺失" for header, pos in zip(required, positions) if pos < 0]
    if errors:
        return errors
    if positions != sorted(positions):
        errors.append("标题交付的三个固定段落顺序错误")
        return errors

    final_section = text[positions[0] + len(required[0]) : positions[1]]
    normalized_lines = [
        re.sub(
            r"^[-*]\s*",
            "",
            line.strip().replace("**", "").replace("__", ""),
        )
        for line in final_section.splitlines()
    ]
    required_fields = (
        ("标题",),
        ("封面主文案",),
        ("封面画面",),
        ("观众会追问", "它击中的问题"),
        ("视频兑现",),
        ("开头衔接",),
    )
    for aliases in required_fields:
        if not any(
            line == field or line.startswith(field + "：") or line.startswith(field + ":")
            for field in aliases
            for line in normalized_lines
        ):
            errors.append(f"“首选组合”缺少字段：{aliases[0]}")

    plain_section = _field_plain_text(final_section)
    opening_type_match = _OPENING_TYPE_RE.search(plain_section)
    if not opening_type_match:
        errors.append(
            "“首选组合”缺少可执行开头类型：source-cold-open、host-narrative 或 hybrid"
        )
        return errors

    opening_type = opening_type_match.group(1)
    source_clips = list(_SOURCE_CLIP_RE.finditer(plain_section))
    host_script = _HOST_SCRIPT_RE.search(plain_section)
    entry = _ENTRY_RE.search(plain_section)

    if opening_type in {"source-cold-open", "hybrid"} and not source_clips:
        errors.append("原片开头必须至少提供一行精确“原片：in --> out｜逐字原话”")
    if opening_type in {"host-narrative", "hybrid"} and not host_script:
        errors.append("补录开头必须提供一行可直接录制的“补录逐字稿”")
    if entry is None:
        errors.append("可执行开头必须提供“进入正片”时间点")

    if source_srt_path is None:
        if opening_type in {"source-cold-open", "hybrid"}:
            errors.append("没有带时间逐字稿时，首选开头不能声称原片 cold open 可执行")
        if entry and entry.group("stamp") != "待定位":
            errors.append("没有带时间逐字稿时，“进入正片”只能标为待定位")
        return errors

    try:
        cues = parse_srt(source_srt_path)
    except ValueError as error:
        errors.append(f"开头定位 SRT 无法解析：{error}")
        return errors
    source_end = cues[-1]["end"]
    cue_starts = [cue["start"] for cue in cues]
    cue_ends = [cue["end"] for cue in cues]

    for clip in source_clips:
        try:
            start = _srt_stamp_seconds(clip.group("start"))
            end = _srt_stamp_seconds(clip.group("end"))
        except ValueError as error:
            errors.append(str(error))
            continue
        if end <= start:
            errors.append(f"原片 in/out 未递增：{clip.group('start')} --> {clip.group('end')}")
            continue
        if start < cues[0]["start"] - 0.001 or end > source_end + 0.001:
            errors.append(
                f"原片范围超出字幕时域：{clip.group('start')} --> {clip.group('end')}"
            )
            continue
        if not any(abs(start - boundary) <= 0.005 for boundary in cue_starts):
            errors.append(f"原片 in 不是 cue 起点：{clip.group('start')}")
        if not any(abs(end - boundary) <= 0.005 for boundary in cue_ends):
            errors.append(f"原片 out 不是 cue 终点：{clip.group('end')}")
        overlapping = [
            " ".join(cue["text"].splitlines())
            for cue in cues
            if cue["end"] > start - 0.001 and cue["start"] < end + 0.001
        ]
        quote_key = _quote_key(clip.group("quote"))
        source_key = _quote_key(" ".join(overlapping))
        if not quote_key or quote_key not in source_key:
            errors.append(
                f"原片引语无法在所给时段逐字核对：{clip.group('start')} --> {clip.group('end')}"
            )

    if entry and entry.group("stamp") == "待定位":
        errors.append("已有带时间逐字稿时，“进入正片”必须给出精确时间点")
    elif entry:
        try:
            entry_seconds = _srt_stamp_seconds(entry.group("stamp"))
        except ValueError as error:
            errors.append(str(error))
        else:
            if entry_seconds < cues[0]["start"] - 0.001 or entry_seconds > source_end + 0.001:
                errors.append(f"“进入正片”超出字幕时域：{entry.group('stamp')}")

    if not speaker_attribution_verified:
        source_and_entry_lines = [match.group(0) for match in source_clips]
        if entry:
            source_and_entry_lines.append(entry.group(0))
        if re.search(r"主持人|嘉宾", "\n".join(source_and_entry_lines)):
            errors.append("没有有效 speaker sidecar 时，不得把原片声音归为主持人或嘉宾")
    return errors


def raise_for_errors(kind: str, errors: list[str]) -> None:
    if errors:
        raise AssetValidationError(f"{kind} QC 未通过：" + "；".join(errors))
