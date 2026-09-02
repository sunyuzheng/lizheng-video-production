#!/usr/bin/env python3
"""Deterministic quality gates for model-generated publishing assets."""

from __future__ import annotations

import re
from pathlib import Path

from tools.subtitle_qc import parse_srt


class AssetValidationError(RuntimeError):
    """A generated asset does not satisfy its delivery contract."""


_CHAPTER_RE = re.compile(
    r"^(?P<stamp>(?:\d{2,}:\d{2}|[1-9]\d*:\d{2}:\d{2}))\s+(?P<title>\S.*)$"
)


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


def validate_title_output(text: str) -> list[str]:
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
    return errors


def raise_for_errors(kind: str, errors: list[str]) -> None:
    if errors:
        raise AssetValidationError(f"{kind} QC 未通过：" + "；".join(errors))
