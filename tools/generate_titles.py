#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_titles.py — 课代表立正播客标题三轮生成工作流 v4

核心变化（相比 v3）：
  - 去掉"入口"分类标注要求：候选不再需要标注入口类型，直接说角度为何有吸引力
  - 放宽高光-标题强约束：强标题优先保留，与高光不配合时注明供终审判断，不自动降级
  - Round 1 使用频道真实高播标题作外部基准（不是模型自评）
  - 三轮全程使用 Claude Code Fable 5，timeout 900s

用法：
  python3 tools/generate_titles.py episode.article.md        # 自动检测同目录 highlights
  python3 tools/generate_titles.py episode.final.srt         # 降级用 SRT
  python3 tools/generate_titles.py episode.article.md --round 0
"""

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.claude_cli import DEFAULT_MODEL, call_claude_file_based

# ── 路径 ────────────────────────────────────────────────────────────────────────

_REPO_DATA = Path(__file__).parent.parent / "data"
_GUIDELINE = _REPO_DATA / "guideline_kedaibiao.md"
_TOP_TITLES = _REPO_DATA / "top_titles.txt"


# ── 资源加载 ────────────────────────────────────────────────────────────────────

def load_guideline() -> str:
    if _GUIDELINE.exists():
        return _GUIDELINE.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Guideline 不存在: {_GUIDELINE}")


def load_top_titles() -> str:
    if _TOP_TITLES.exists():
        return _TOP_TITLES.read_text(encoding="utf-8").strip()
    return ""


def find_highlights(content_path: Path, stem: str, output_dir: Path | None = None) -> str:
    """自动检测输出目录或同目录下是否有 {stem}.highlights.md"""
    search_dirs = []
    if output_dir:
        search_dirs.append(output_dir)
    search_dirs.append(content_path.parent)
    for base in search_dirs:
        h_path = base / f"{stem}.highlights.md"
        if h_path.exists():
            return h_path.read_text(encoding="utf-8")
    return ""


# ── SRT 文本提取 ────────────────────────────────────────────────────────────────

def srt_to_text(srt_path: Path, max_chars: int = 6000) -> str:
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
    text = " ".join(lines)
    return text[:max_chars] + "…（已截断）" if len(text) > max_chars else text


# ── Round 0：内容理解 + 高光驱动标题广撒网 ──────────────────────────────────────

ROUND0_WITH_HIGHLIGHTS = """\
你是课代表立正频道的资深标题编辑。

## 频道 Guideline

{guideline}

---

## 视频高光片段（视频开头会展示的内容）

{highlights}

---

## 完整内容（背景参考）

{content}

---

先想清楚这期内容：中心命题是什么，谁会被打动，高光和完整内容各自的价值在哪里。

然后生成足够多样的标题候选。这期内容强在哪就从哪进，不要为了多样性凑数。每个候选必须有内容里真实存在的素材支撑，不发明没有的东西。不需要给候选标注框架类型，直接说清楚这个角度为什么有吸引力。

标题和高光有分工：标题不描述高光本身，而是描述高光背后更大的问题——标题让人想点进来，高光让人想看完。

最后说明你认为本期最值得做的 2-3 个方向，以及为什么。
"""

ROUND0_WITHOUT_HIGHLIGHTS = """\
你是课代表立正频道的资深标题编辑。

## 频道 Guideline

{guideline}

---

## 本期内容

{content}

---

先理解这期内容：最核心的洞见是什么，最有张力的故事或时刻在哪里，谁会最想看这期。

然后生成足够多样的标题候选。这期内容强在哪就从哪进，每个候选有真实内容支撑。不需要给候选标注框架类型，直接说清楚这个角度为什么有吸引力。

最后说明你认为本期最值得做的 2-3 个方向。
"""


# ── Round 1：外部基准对比 + 差距分析 ──────────────────────────────────────────

ROUND1_PROMPT = """\
你是课代表立正频道的独立标题评审。

频道：帮听众把说不清楚的问题想清楚。受众：已经做得不错但感觉卡住的人，想要底层框架，反感说教和结论先行。好标题：真实好奇心 + 预告值得投入 + 转发者显得清醒。失败：结论当标题 / 宽泛承诺 / 说教 / AI公文感。

## 频道真实高播标题（用来校准判断）

{top_titles}

---

{highlights_section}

## Round 0 候选标题

{round0}

---

从 Round 0 候选里找出真正有潜力的，说明每条的优势和问题。

然后诊断整体：这期内容最有价值的角度有没有被充分探索？有没有某个候选差了一个词——直接说哪个词改成什么。{highlight_alignment_check}

最后给 Round 2 具体可执行的指令，每条指向一个真实的盲区或改进方向（"利用内容里X这个细节" 而不是 "更具体"）。
"""


# ── Round 2：补强 + 最终选题 ──────────────────────────────────────────────────

ROUND2_PROMPT = """\
你是课代表立正频道的终审编辑。这是最终决定。

{highlights_section}

## Round 0 全部候选

{round0}

## Round 1 评审

{round1}

---

按 Round 1 的指令补充新标题，填补盲区。

然后从所有候选里选出最终标题。数量取决于质量，通常 6-10 个。选择标准按优先级：诚实性（视频里确实有，不剧透高光）> 真实好奇心 > 覆盖不同受众群体 > 转发测试（有独立判断的 30 多岁职场人愿意分享）。如果某个标题明显很强但与高光配合不完美，保留它并注明，不要因为高光配合度低就放弃强标题。禁止：结论全说完 / 宽泛承诺 / 说教语气。

为排名前 5 的标题各给一条封面建议。封面和标题各司其职，互补不重复：
- 访谈视频：从嘉宾原话提炼 3 句金句，每句让人看到都想知道来龙去脉；可以轻微 paraphrase 但不能夸大
- 单口视频：3-10 字冲击文字，标题做延伸阐释

每条封面建议说清楚：主内容是什么、画面怎么构成、封面和标题如何互补。

## 输出文件格式（这是给剪辑师和主播单独阅读的交付文件，不是给你自己的笔记）

固定三段结构，顺序不可变：

1. `## 最终标题`——置顶，按推荐顺序编号。每条带标题文本（标注字数）和一段自我完备的推荐理由，最后给一行投放建议。
2. `## 前 5 标题的封面建议`
3. `## 备选`——每条写出标题完整原文和未进最终的原因。

硬性要求：
- **禁止出现任何轮次代号**（A1、B3、R2-4、「Round 1 指令」「Round 0 候选」这类）。读这个文件的人没看过前几轮的工作文件。需要对比其他候选时，直接引用那条标题的原文。
- 按指令补充候选、填补盲区这些推理过程在心里完成，不要写进文件——文件里只留结果和结果的理由。
"""


# ── 工作流 ───────────────────────────────────────────────────────────────────────

def run_round0(content: str, highlights: str, workspace: Path) -> Path:
    out = workspace / "round0_candidates.md"
    guideline = load_guideline()

    if highlights:
        prompt = ROUND0_WITH_HIGHLIGHTS.format(
            guideline=guideline, highlights=highlights, content=content
        )
    else:
        prompt = ROUND0_WITHOUT_HIGHLIGHTS.format(
            guideline=guideline, content=content
        )

    print("    Round 0：理解内容 + 多角度生成候选…", flush=True)
    call_claude_file_based(prompt, out, model=DEFAULT_MODEL)
    print(f"    ✓ {out.name} 已写入")
    return out


def run_round1(round0: Path, highlights: str, workspace: Path) -> Path:
    out = workspace / "round1_review.md"
    r0 = round0.read_text(encoding="utf-8")
    top_titles = load_top_titles()

    if highlights:
        highlights_section = f"## 视频高光片段\n\n{highlights}\n\n---\n"
        highlight_alignment_check = "\n- Round 0 候选中，哪些与高光形成了好的分工（标题创造期待，高光验证期待）？如果有标题明显很强但与高光不完全配合，也请保留并注明，让终审决定。"
    else:
        highlights_section = ""
        highlight_alignment_check = ""

    prompt = ROUND1_PROMPT.format(
        top_titles=top_titles,
        highlights_section=highlights_section,
        round0=r0,
        highlight_alignment_check=highlight_alignment_check,
    )

    print("    Round 1：外部基准对比 + 差距诊断…", flush=True)
    call_claude_file_based(prompt, out, model=DEFAULT_MODEL)
    print(f"    ✓ {out.name} 已写入")
    return out


def run_round2(round0: Path, round1: Path, highlights: str, final_out: Path) -> Path:
    r0 = round0.read_text(encoding="utf-8")
    r1 = round1.read_text(encoding="utf-8")

    if highlights:
        highlights_section = f"## 视频高光片段（供参考：标题和高光最好形成分工，但明显强的标题优先保留）\n\n{highlights}\n\n---\n"
    else:
        highlights_section = ""

    prompt = ROUND2_PROMPT.format(
        highlights_section=highlights_section, round0=r0, round1=r1
    )

    print("    Round 2：补强 + 最终选题…", flush=True)
    call_claude_file_based(prompt, final_out, model=DEFAULT_MODEL)
    print(f"    ✓ {final_out.name} 已写入")
    return final_out


# ── 主流程 ──────────────────────────────────────────────────────────────────────

def generate_titles(
    content_path: Path,
    stop_at_round: int = 2,
    output_dir: Path | None = None,
    workspace_dir: Path | None = None,
    stem: str | None = None,
) -> Path:
    episode_stem = stem or content_path.with_suffix("").stem
    for suffix in (".article", ".final", ".corrected", ".qwen"):
        if episode_stem.endswith(suffix):
            episode_stem = episode_stem[: -len(suffix)]
            break

    out_dir = output_dir or content_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace_base = workspace_dir or out_dir
    workspace_base.mkdir(parents=True, exist_ok=True)
    workspace = workspace_base / f"{episode_stem}_title_ws"
    workspace.mkdir(exist_ok=True)
    final_out = out_dir / f"{episode_stem}.titles.md"

    # 读取主内容
    if content_path.suffix == ".md":
        content = content_path.read_text(encoding="utf-8")
        if len(content) > 6000:
            content = content[:6000] + "…（已截断）"
    else:
        content = srt_to_text(content_path, max_chars=6000)

    # 自动检测高光文件
    highlights = find_highlights(content_path, episode_stem, output_dir=out_dir)
    if highlights:
        print(f"    ✓ 发现高光文件 {episode_stem}.highlights.md，高光驱动模式启动")
    else:
        print(f"    ! 未找到高光文件，使用完整内容模式")

    r0 = run_round0(content, highlights, workspace)
    if stop_at_round == 0:
        return r0

    r1 = run_round1(r0, highlights, workspace)
    if stop_at_round == 1:
        return r1

    return run_round2(r0, r1, highlights, final_out)


def main() -> None:
    parser = argparse.ArgumentParser(description="课代表立正播客标题三轮生成 v3（高光驱动）")
    parser.add_argument("content", help="输入文件：.article.md 或 .final.srt")
    parser.add_argument(
        "--round", type=int, default=2, choices=[0, 1, 2],
        help="停在第几轮（0=只生成候选，1=+评审，2=完整）",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help="最终标题输出目录（默认与输入文件同目录）",
    )
    parser.add_argument(
        "--workspace-dir",
        default=None,
        help="标题三轮过程文件目录（默认与最终标题同目录）",
    )
    args = parser.parse_args()

    content_path = Path(args.content).resolve()
    if not content_path.exists():
        print(f"错误: 文件不存在: {content_path}")
        sys.exit(1)

    print(f"  标题生成：{content_path.name} …", flush=True)
    try:
        out = generate_titles(
            content_path,
            stop_at_round=args.round,
            output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
            workspace_dir=Path(args.workspace_dir).resolve() if args.workspace_dir else None,
        )
        print(f"  ✓ 标题已写入：{out.name}")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
