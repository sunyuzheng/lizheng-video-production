#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_youtube_description.py — 从 final.srt 生成 YouTube 介绍和章节。

输出：<video>.youtube-description.txt
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.content_cli import DEFAULT_CONTENT_MODEL, call_content_file_based
from tools.asset_qc import raise_for_errors, validate_youtube_description
from tools.srt_text import timed_text_from_srt


def srt_to_timed_text(srt_path: Path, window_seconds: int = 60) -> str:
    return timed_text_from_srt(srt_path, window_seconds=window_seconds)


def _episode_stem(path: Path) -> str:
    stem = path.with_suffix("").stem
    for suffix in (".final", ".corrected", ".qwen"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


PROMPT = """\
根据字幕写一份可直接发布的 YouTube description。

开头直接进入 substance：用具体事实、问题、人物经历或关键机制概括这期真正讨论了什么，优先 surface 观众不看完整视频很难知道的信息。不要先写“本期适合谁看”“这期最有价值的一条线”“为什么值得看”之类元叙述；让内容本身建立兴趣。字幕没有提供的嘉宾身份、数字或背景不要发明。

表达直接、有条理、平实，保留内容的专业性与人物感，不写营销号腔，也不要把多个具体问题压成“认知升级、底层逻辑、AI 时代的思考”等空泛总结。

如果这期有嘉宾，在介绍之后、“章节：”之前，为每位嘉宾单独写一行，格式严格为“嘉宾：姓名，身份”。身份写这位嘉宾现在的职位和所在机构，必要时加一个最要紧的过往经历；用一个短语，不超过40个字，不加句号。只写字幕里交代过的，没有交代就只写“嘉宾：姓名”。没有嘉宾的单人口播不写这一行。嘉宾目录会把这一行直接用作这位嘉宾的简介，所以宁可少写，不能写错。

也给我 YouTube 适合的章节。时间戳通常用 mm:ss，超过一小时可用 h:mm:ss，必须从 00:00 开始。至少 3 章，相邻章节至少间隔 10 秒。章节数不要太多，简洁一些。时间戳要根据字幕里的真实时间判断，不能编。

输出必须是纯 txt 内容，可以直接复制粘贴到 YouTube description：
1. 先写 2-4 段介绍。
2. 有嘉宾时，接着每位嘉宾一行“嘉宾：姓名，身份”。
3. 然后写一行“章节：”。
4. 每个章节一行，格式严格为 “mm:ss 章节标题”（超过一小时可用 “h:mm:ss 章节标题”）。
5. 不要 Markdown 标题，不要项目符号，不要解释过程。

字幕如下：

---
{transcript}
---
"""


# “嘉宾：姓名，身份”这一行（2026-11-10 定，有嘉宾的节目固定带）是嘉宾目录的输入：Open Context
# 发布时读视频简介里的这一行，直接填成嘉宾页上的简介（它的 scripts/guest_profiles.py）。
# 改这一行的写法前先看那边认哪几种格式。

# 超线性学院对外广告语与入口（2026-10-30 定，逐字使用）；owner：course-marketing-system 品牌平台 §7。
COMMUNITY_FOOTER = (
    "不怕AI天天变，就来超线性学院。学点真本事，做点真东西。\n"
    "免费加入：https://www.superlinear.academy/ （也可以在App Store搜「超线性学院」）"
)


def with_community_footer(text: str) -> str:
    """把固定的社区一段放在“章节：”上方，章节仍是最后一块。"""
    if COMMUNITY_FOOTER in text:
        return text
    lines = text.replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        if line.strip() in {"章节：", "章节:"}:
            head = "\n".join(lines[:index]).rstrip()
            tail = "\n".join(lines[index:])
            return f"{head}\n\n{COMMUNITY_FOOTER}\n\n{tail}"
    return f"{text.rstrip()}\n\n{COMMUNITY_FOOTER}\n"


def generate_youtube_description(
    srt_path: Path,
    output_dir: Path | None = None,
    stem: str | None = None,
) -> Path:
    out_dir = output_dir or srt_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    episode_stem = stem or _episode_stem(srt_path)
    output_path = out_dir / f"{episode_stem}.youtube-description.txt"
    candidate_path = out_dir / f".{episode_stem}.youtube-description.candidate.txt"
    invalid_path = out_dir / f"{episode_stem}.youtube-description.invalid.txt"
    candidate_path.unlink(missing_ok=True)
    transcript = srt_to_timed_text(srt_path)
    prompt = PROMPT.format(transcript=transcript)
    call_content_file_based(prompt, candidate_path, model=DEFAULT_CONTENT_MODEL)
    candidate_text = candidate_path.read_text(encoding="utf-8")
    errors = validate_youtube_description(candidate_text, srt_path)
    if errors:
        invalid_path.unlink(missing_ok=True)
        candidate_path.replace(invalid_path)
        try:
            raise_for_errors("YouTube description", errors)
        except Exception as error:
            raise type(error)(f"{error}；诊断稿：{invalid_path}") from error
    candidate_path.write_text(with_community_footer(candidate_text), encoding="utf-8")
    candidate_path.replace(output_path)
    invalid_path.unlink(missing_ok=True)
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
