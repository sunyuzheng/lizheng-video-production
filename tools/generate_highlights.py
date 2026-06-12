#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_highlights.py — 从 SRT 逐字稿提取高光片段 v2

核心逻辑：
  1. 优先检测 SRT 末尾追加的真实高光字幕（00:00:xx 时间戳，编辑者亲手选定）
     如果存在，用它作为权威高光来源进行分析
  2. 不存在时，用分区采样全文扫描

用法：
  python3 tools/generate_highlights.py episode.final.srt
"""

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.claude_cli import DEFAULT_MODEL, call_claude_file_based

_REPO_DATA = Path(__file__).parent.parent / "data"
_GUIDELINE = _REPO_DATA / "guideline_kedaibiao.md"


def load_guideline() -> str:
    if _GUIDELINE.exists():
        return _GUIDELINE.read_text(encoding="utf-8")
    return ""


# ── SRT 解析工具 ───────────────────────────────────────────────────────────────

def srt_to_text(srt_path: Path) -> str:
    """提取 SRT 全文纯文本"""
    content = srt_path.read_text(encoding="utf-8")
    lines = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->", line):
            continue
        lines.append(line)
    return " ".join(lines)


def _format_timestamp(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours:
        return f"{hours:02d}:{minutes:02d}"
    return f"{minutes:02d}:00"


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
    """提取 SRT 文本，并按时间窗口合并，保留高光定位需要的粗时间戳。"""
    content = srt_path.read_text(encoding="utf-8")
    buckets: dict[int, list[str]] = {}
    current_start: int | None = None
    current_lines: list[str] = []

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
            current_start = start_seconds
            continue
        current_lines.append(line)
    flush_current()

    return "\n".join(
        f"[{_format_timestamp(start)}] {' '.join(texts)}"
        for start, texts in sorted(buckets.items())
    )


def extract_appended_highlights(srt_path: Path) -> str:
    """
    检测 SRT 末尾是否有追加的真实高光字幕。

    特征：主内容时间戳在 00:01:xx 以后，高光字幕追加在末尾但时间戳
    重置为 00:00:xx（编辑者从视频开头截取后追加到 SRT 文件末尾）。

    返回高光文本，或空字符串（未检测到）。
    """
    content = srt_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    appended_start = -1
    seen_main_content = False
    for i, line in enumerate(lines):
        start_seconds = _parse_start_seconds(line)
        if start_seconds is None:
            continue
        if start_seconds >= 60:
            seen_main_content = True
            continue
        if seen_main_content and i >= len(lines) * 0.3:
            appended_start = i
            break

    if appended_start == -1:
        return ""

    # 提取从该位置开始的所有文本
    texts = []
    for line in lines[appended_start:]:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->", line):
            continue
        texts.append(line)

    return " ".join(texts)


def sample_content(text: str, max_chars: int = 14000) -> str:
    """分区采样确保覆盖视频全程"""
    if len(text) <= max_chars:
        return text
    chunk = max_chars // 4
    total = len(text)
    parts = []
    labels = ["【视频前段】", "【视频中前段】", "【视频中后段】", "【视频后段】"]
    for i in range(4):
        start = int(i * total / 4)
        end = min(start + chunk, total)
        parts.append(labels[i] + "\n" + text[start:end])
    return "\n\n[...省略...]\n\n".join(parts)


# ── Prompts ───────────────────────────────────────────────────────────────────

HIGHLIGHTS_FROM_ACTUAL = """\
你是课代表立正频道的内容编辑。

## 频道 Guideline（参考）

{guideline}

---

以下是编辑者已亲手选定的视频开头高光片段（30-90秒）。这是真实使用的开场钩子。

## 实际高光文本

{highlights_text}

---

## 完整内容（背景参考）

{content_sample}

---

先判断这是访谈还是单口，这决定分析框架。

**访谈**：分析每段高光是否同时完成两件事——
① Vantage point：这句话如何隐性建立嘉宾权威？（不是介绍身份，而是"这句话只有在那个位置待过的人才能说出来"）
② Cognitive gap：观众听完会产生什么具体问题？

**单口**：分析这段话如何代表主播的核心判断，以及制造了什么悬念。

输出：视频类型和主发言人、中心命题、受众分析、每段高光的时间戳 + 引用原话 + vantage point 分析（仅访谈）+ cognitive gap + 为什么值得观众跳转观看 + 在整体叙事中的位置、整组高光的叙事弧线以及各段之间的组合逻辑。

输出文件会被剪辑师单独阅读：不要引用 guideline 的内部编号或代号（如「入口4」「框架B」），所有理由用大白话写到自我完备。
"""

HIGHLIGHTS_FROM_SCAN = """\
你是课代表立正频道的内容编辑，负责为视频选取开场高光片段（30-90秒）。

## 频道 Guideline（参考）

{guideline}

---

## 本期内容

{content}

---

先判断这是访谈还是单口，这决定高光选取的核心逻辑。

**访谈**：好的高光同时完成两件事——
① Vantage point（隐性建立嘉宾权威）：这句话，只有在那个位置待过、经历过那些事的人才能说出来。不是靠介绍身份，而是靠嘉宾说的内容本身，让观众感受到「这个人见过我没见过的东西」。有 vantage point 信号的话，往往来自跨行业横向比较、顶层内部视角、亲历重要时刻的第一手描述、深度操盘后的反常识判断。
② Cognitive gap（制造观众脑子里的问题）：观众听完产生一个还没被满足的具体问题——「为什么这么说？怎么来的？后来呢？」

对每段候选片段问：「只有在嘉宾那个位置的人才能说这句话吗？」+「听完会产生什么具体问题？」两个都是 yes 才是强力候选。

访谈输出 **6-8 段**候选高光，覆盖嘉宾的不同侧面（经历故事、行业判断、反常识观点、产品/技术关键解释、创业选择……），让编辑从中选组合。每段必须带可跳转时间戳，说明观众为什么应该跳到这里看。

**单口**：选主播的核心论断，能代表这期内容最有价值的判断，让人感觉「这个人真的想清楚了」。输出 3-4 段候选。

两种类型都要做到：候选之间覆盖不同侧面，不要把所有候选集中在同一个角度。几段合起来能讲一个比任何单段都更大的故事——叙事弧线在最后说明。

只用原话，不改写不总结。

输出：视频类型和主发言人、中心命题（一句话）、受众分析（现有受众 + 潜在扩展人群）、每段候选的时间戳 + 引用原话 + vantage point（仅访谈）+ cognitive gap + 观看价值说明 + 在整体叙事中的位置、整组候选的叙事弧线和推荐组合。

输出文件会被剪辑师单独阅读：不要引用 guideline 的内部编号或代号（如「入口4」「框架B」），所有理由用大白话写到自我完备。
"""


# ── 主逻辑 ────────────────────────────────────────────────────────────────────

def _episode_stem(path: Path) -> str:
    stem = path.with_suffix("").stem
    for suffix in (".final", ".corrected", ".qwen", ".article"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def generate_highlights(
    srt_path: Path,
    output_dir: Path | None = None,
    stem: str | None = None,
) -> Path:
    episode_stem = stem or _episode_stem(srt_path)
    out_dir = output_dir or srt_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{episode_stem}.highlights.md"

    # 读取内容
    if srt_path.suffix == ".md":
        full_text = srt_path.read_text(encoding="utf-8")
        actual_highlights = ""
    else:
        full_text = srt_to_timed_text(srt_path)
        actual_highlights = extract_appended_highlights(srt_path)

    guideline = load_guideline()

    if actual_highlights:
        print(f"    ✓ 检测到编辑者亲选的高光字幕（{len(actual_highlights)} 字），优先使用")
        content_sample = sample_content(full_text, max_chars=8000)
        prompt = HIGHLIGHTS_FROM_ACTUAL.format(
            guideline=guideline,
            highlights_text=actual_highlights,
            content_sample=content_sample,
        )
    else:
        print(f"    ! 未检测到追加高光，扫描全文选取")
        content = sample_content(full_text, max_chars=14000)
        prompt = HIGHLIGHTS_FROM_SCAN.format(guideline=guideline, content=content)

    print("    高光分析中…", flush=True)
    call_claude_file_based(prompt, output_path, model=DEFAULT_MODEL)
    print(f"    ✓ {output_path.name} 已写入")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="从 SRT 提取/分析视频高光片段 v2")
    parser.add_argument("content", help="输入文件：.final.srt / .corrected.srt / .article.md")
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help="输出目录（默认与输入文件同目录）",
    )
    args = parser.parse_args()

    srt_path = Path(args.content).resolve()
    if not srt_path.exists():
        print(f"错误: 文件不存在: {srt_path}")
        sys.exit(1)

    print(f"  高光提取：{srt_path.name} …", flush=True)
    try:
        out = generate_highlights(
            srt_path,
            output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
        )
        print(f"  ✓ 高光已写入：{out.name}")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
