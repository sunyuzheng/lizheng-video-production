#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_highlights.py — 从 SRT 逐字稿提取高光片段 v3

核心逻辑：
  1. 优先检测 SRT 末尾追加的真实高光字幕（00:00:xx 时间戳，编辑者亲手选定）
     如果存在，用它作为权威高光来源进行分析
  2. 不存在时，用分区采样全文扫描

用法：
  python3 tools/generate_highlights.py episode.final.srt
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.claude_cli import DEFAULT_MODEL, call_claude_file_based
from tools.speaker_sidecar import load_validated_speaker_srt
from tools.srt_text import timed_text_from_srt
from tools.subtitle_qc import parse_srt

_REPO_DATA = Path(__file__).parent.parent / "data"
_GUIDELINE = _REPO_DATA / "guideline_kedaibiao.md"


def load_guideline() -> str:
    if _GUIDELINE.exists():
        return _GUIDELINE.read_text(encoding="utf-8")
    return ""


# ── SRT 解析工具 ───────────────────────────────────────────────────────────────

def _format_exact_timestamp(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def srt_to_timed_text(srt_path: Path, window_seconds: int = 60) -> str:
    """提取 SRT 文本，并按时间窗口合并，保留高光定位需要的粗时间戳。"""
    return timed_text_from_srt(srt_path, window_seconds=window_seconds)


def extract_appended_highlights(srt_path: Path) -> str:
    """
    检测 SRT 末尾是否有追加的真实高光字幕。

    特征：主内容时间戳在 00:01:xx 以后，高光字幕追加在末尾但时间戳
    重置为 00:00:xx（编辑者从视频开头截取后追加到 SRT 文件末尾）。

    返回高光文本，或空字符串（未检测到）。
    """
    cues = parse_srt(srt_path)
    appended_start = -1
    seen_main_content = False
    for i, cue in enumerate(cues):
        start_seconds = int(cue["start"])
        if start_seconds >= 60:
            seen_main_content = True
            continue
        if seen_main_content and i >= len(cues) * 0.3:
            appended_start = i
            break

    if appended_start == -1:
        return ""

    # 保留追加段自己的时间戳，供剪辑师在开场 timeline 中定位。
    texts = []
    for cue in cues[appended_start:]:
        stamp = _format_exact_timestamp(int(cue["start"]))
        text = " ".join(cue["text"].splitlines()).strip()
        texts.append(f"[{stamp}] {text}")

    return "\n".join(texts)


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

以下是编辑者已亲手选定的视频开头高光片段。这是重要的编辑判断，不需要重新假装从零选片；你的任务是理解它们为什么成立、组合后是否 surface 了本期真正的 substance，以及有没有明显缺口。

## 实际高光文本

{highlights_text}

---

## 完整内容（背景参考）

{content_sample}

---

先判断这是访谈还是单口，并找出其中不可替代的事实、机制、人物选择和现场转折。

访谈可以用 vantage point、cognitive gap、mechanism、person 和 arc 五个视角理解；它们是帮助看见价值的镜头，不要求每段全部满足。单口重点看论断的证据、推理来路和它在整期里的作用。

每段保留可核对的时间戳和定位原话，再用编辑语言说明 substance、目标观众为什么会在意，以及它与其他片段怎样组合。解释可以总结；定位引文本身不要改写。

输出：视频类型与主发言人、本期真正的问题、具体读者处境、逐段时间戳与定位原话、逐段 substance／观看价值／叙事作用，以及整组高光已经形成的弧线。若缺少一段关键解释或人物线，明确指出应回到正文哪一部分寻找，不为了形式硬补。

输出文件会被剪辑师单独阅读：不要引用 guideline 的内部编号或代号（如「入口4」「框架B」），所有理由用大白话写到自我完备。
"""

HIGHLIGHTS_FROM_SCAN = """\
你是课代表立正频道的内容编辑，负责从长视频中 surface 最值得先让观众看到的片段。

## 频道 Guideline（参考）

{guideline}

---

## 本期内容

{content}

---

先判断这是访谈还是单口，再找材料中不可替代的事实、机制、人物选择、现场修正和重要解释。不要把最响亮或最戏剧性的句子自动当成最好的开场。

访谈可用五个视角比较候选：
- vantage point：只有这个位置或经历的人容易知道；
- cognitive gap：自然引出一个具体的下一问；
- mechanism：把“发生了什么”推进到“为什么”；
- person：显出嘉宾怎样选择、学习、犹豫或修正；
- arc：与其他片段组合后形成更大的问题。

vantage point 与 cognitive gap 同时成立通常有力，但完整解释、人物关系、幽默和情绪也可能成立，不要把两个 yes 当筛选器。

候选数量由材料决定。长而散的访谈通常需要多给几段让编辑组合，短而集中的内容可以更少；不要为了达到数字重复同一角度。

单口不仅选结论，也看支撑结论的事实、定义和推理转折。一个响亮判断若离开上下文就变成空话，不适合单独做高光。

每段带可跳转时间戳和可核对的定位原话，再说明 substance、目标观众为什么会在意、在整期中的作用。定位引文保持原话；编辑解释可以总结和比较。

输出：视频类型和主发言人、本期真正的问题、具体读者处境、逐段候选的时间戳 + 定位原话 + substance + 观看价值 + 叙事作用，最后给出一到两种有明确逻辑的组合建议。没有进入开场但对完整理解重要的内容，可以列为章节而不是硬塞进高光。

输出文件会被剪辑师单独阅读：不要引用 guideline 的内部编号或代号（如「入口4」「框架B」），所有理由用大白话写到自我完备。
"""


# ── 主逻辑 ────────────────────────────────────────────────────────────────────

def _episode_stem(path: Path) -> str:
    stem = path.with_suffix("").stem
    for suffix in (".speaker_labeled", ".final", ".corrected", ".qwen", ".article"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _read_speaker_labeled(content_path: Path, output_dir: Path, episode_stem: str) -> str:
    return load_validated_speaker_srt(
        content_path,
        [
        output_dir / f"{episode_stem}.speaker_labeled.srt",
        content_path.parent / f"{episode_stem}.speaker_labeled.srt",
        ],
    )


def generate_highlights(
    srt_path: Path,
    output_dir: Path | None = None,
    stem: str | None = None,
) -> Path:
    episode_stem = stem or _episode_stem(srt_path)
    out_dir = output_dir or srt_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{episode_stem}.highlights.md"
    candidate_path = out_dir / f".{episode_stem}.highlights.candidate.md"
    candidate_path.unlink(missing_ok=True)

    # 读取内容。访谈如果有 speaker_labeled.md/srt，优先用它做归因参考。
    speaker_labeled = _read_speaker_labeled(srt_path, out_dir, episode_stem)
    if srt_path.suffix == ".md":
        full_text = srt_path.read_text(encoding="utf-8")
        actual_highlights = ""
    else:
        full_text = srt_to_timed_text(srt_path)
        actual_highlights = extract_appended_highlights(srt_path)
    if speaker_labeled:
        full_text = (
            "【说话人标注稿，访谈归因优先信源】\n"
            "只有明确标成嘉宾/主持人的内容，才可以写成「嘉宾说 / 主持人说」。"
            "UNKNOWN 或 MIXED 段落不得强行归因。\n\n"
            + speaker_labeled
        )

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
    call_claude_file_based(prompt, candidate_path, model=DEFAULT_MODEL)
    if not candidate_path.read_text(encoding="utf-8").strip():
        raise RuntimeError("高光模型返回空内容")
    candidate_path.replace(output_path)
    print(f"    ✓ {output_path.name} 已写入")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="从 SRT 提取/分析视频高光片段 v3")
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
