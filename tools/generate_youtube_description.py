#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_youtube_description.py — 从 final.srt 生成 YouTube 介绍和章节。

输出：<video>.youtube-description.txt
"""

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.claude_cli import DEFAULT_MODEL, call_claude_file_based


def _format_timestamp(seconds: int) -> str:
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def _parse_start_seconds(time_line: str) -> int | None:
    match = re.match(
        r"^(\d{2}):(\d{2}):(\d{2})[,\.]\d{3}\s*-->",
        time_line.strip(),
    )
    if not match:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def srt_to_timed_text(srt_path: Path, window_seconds: int = 60) -> str:
    content = srt_path.read_text(encoding="utf-8")
    buckets: dict[int, list[str]] = {}
    current_start: int | None = None
    current_lines: list[str] = []
    last_start: int | None = None
    seen_main_content = False

    def flush_current() -> None:
        nonlocal current_start, current_lines
        if current_start is None or not current_lines:
            current_start = None
            current_lines = []
            return
        bucket_start = (current_start // window_seconds) * window_seconds
        buckets.setdefault(bucket_start, []).append("".join(current_lines))
        current_start = None
        current_lines = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            flush_current()
            continue
        if re.match(r"^\d+$", line):
            continue
        start_seconds = _parse_start_seconds(line)
        if start_seconds is not None:
            flush_current()
            if (
                seen_main_content
                and last_start is not None
                and start_seconds + 30 < last_start
            ):
                break
            if start_seconds >= 60:
                seen_main_content = True
            last_start = start_seconds
            current_start = start_seconds
            continue
        current_lines.append(line)
    flush_current()

    return "\n".join(
        f"[{_format_timestamp(start)}] {' '.join(texts)}"
        for start, texts in sorted(buckets.items())
    )


def _episode_stem(path: Path) -> str:
    stem = path.with_suffix("").stem
    for suffix in (".final", ".corrected", ".qwen"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


PROMPT = """\
按照给你的字幕，提炼一下视频的内容，我用作YouTube的介绍。

这个内容应该吸引人：开宗明义，这视频能给什么样的观众带来什么新信息，他们为什么值得看看；但表达要直接、有条理、平实，不要营销号腔。

也给我 YouTube 适合的章节。时间戳必须是 mm:ss 格式，从 00:00 开始。章节数不要太多，简洁一些。时间戳要根据字幕里的真实时间判断，不能编。

输出必须是纯 txt 内容，可以直接复制粘贴到 YouTube description：
1. 先写 2-4 段介绍。
2. 然后写一行“章节：”。
3. 每个章节一行，格式严格为 “mm:ss 章节标题”。
4. 不要 Markdown 标题，不要项目符号，不要解释过程。

字幕如下：

---
{transcript}
---
"""


def generate_youtube_description(
    srt_path: Path,
    output_dir: Path | None = None,
    stem: str | None = None,
) -> Path:
    out_dir = output_dir or srt_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    episode_stem = stem or _episode_stem(srt_path)
    output_path = out_dir / f"{episode_stem}.youtube-description.txt"
    transcript = srt_to_timed_text(srt_path)
    prompt = PROMPT.format(transcript=transcript)
    call_claude_file_based(prompt, output_path, model=DEFAULT_MODEL)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="SRT 字幕 → YouTube description + chapters")
    parser.add_argument("srt", help="输入 SRT 文件路径（通常为 .final.srt）")
    parser.add_argument("-o", "--output-dir", default=None, help="输出目录（默认与输入文件同目录）")
    args = parser.parse_args()

    srt_path = Path(args.srt).resolve()
    if not srt_path.exists():
        print(f"错误: 文件不存在: {srt_path}")
        sys.exit(1)
    print(f"  生成 YouTube description：{srt_path.name} …", flush=True)
    out = generate_youtube_description(
        srt_path,
        output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
    )
    print(f"  ✓ YouTube description 已写入：{out.name}")


if __name__ == "__main__":
    main()
