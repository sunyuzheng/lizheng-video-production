#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_titles.py — 课代表立正播客标题三轮生成工作流 v4

核心变化（相比 v3）：
  - 从本期不可替代的事实、机制、人物与受众 stakes 出发，不按标题类型填格子
  - 高光、标题与封面按当前平台共同判断，不维护永久的“不重复／不剧透”规则
  - Round 1 使用频道真实高播标题作外部样本，同时保留新内容超出历史样本的空间
  - 三轮全程使用 Claude CLI（模型由 CLI 默认或环境变量选择），timeout 900s

用法：
  python3 tools/generate_titles.py episode.article.md        # 自动检测同目录 highlights
  python3 tools/generate_titles.py episode.final.srt         # 降级用 SRT
  python3 tools/generate_titles.py episode.article.md --round 0
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.claude_cli import DEFAULT_MODEL, call_claude_file_based
from tools.asset_qc import raise_for_errors, validate_title_output
from tools.srt_text import plain_text_from_srt

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
    return plain_text_from_srt(srt_path, max_chars=max_chars)


# ── Round 0：内容理解 + 高光驱动标题广撒网 ──────────────────────────────────────

ROUND0_WITH_HIGHLIGHTS = """\
你是课代表立正频道的资深标题编辑。

## 频道 Guideline

{guideline}

---

## 视频高光片段

{highlights}

---

## 完整内容（背景参考）

{content}

---

先把本期不可替代的 substance 找出来：具体事实、数字、机制、人物选择、第一手比较、重要定义，以及主持人的追问怎样改变了问题。再说清目标观众已经知道什么、卡在哪里、需要什么新信息。

从真正强的材料出发生成候选，不按“问句、反常识、故事、框架”等类型平均填格子。问题、结论、人物身份、数字、场景和结构性张力都可以使用；每个候选都要指出它依赖哪一项真实材料，以及哪类观众会立刻明白为什么值得点开。

把标题、高光和封面看成一个组合。它们可以分工，也可以适度重复来降低陌生观众的理解成本；不要为了遵守抽象规则牺牲一个准确、有力的标题。

最后说明本期最值得深入的 2–3 个方向：它们的证据、目标观众和取舍分别是什么。
"""

ROUND0_WITHOUT_HIGHLIGHTS = """\
你是课代表立正频道的资深标题编辑。

## 频道 Guideline

{guideline}

---

## 本期内容

{content}

---

先找本期不可替代的事实、机制、人物选择和现场转折，再判断哪类观众会在意、他们以前缺少什么信息。

从真正强的材料出发生成候选，不按标题类型填格子。问题、结论、身份、数字、场景和结构性张力都可以使用；每个候选都要指出真实证据与目标观众。

最后说明本期最值得深入的 2–3 个方向，以及各自的证据和取舍。
"""


# ── Round 1：外部基准对比 + 差距分析 ──────────────────────────────────────────

ROUND1_PROMPT = """\
你是课代表立正频道的独立标题评审。

## 当前频道判断基准

{guideline}

---

## 频道真实高播标题（用来校准判断）

{top_titles}

---

{highlights_section}

## Round 0 候选标题

{round0}

---

先独立重读材料线索，再判断 Round 0 哪些候选真正抓住了本期 substance。说明优势与问题时落到具体词、观众理解和内容证据，不用“更有冲击力”“更具体”这类空评价。

然后诊断整体：最有价值的事实、机制、人物关系和 stakes 是否被充分探索？大众观众是否看得懂嘉宾／术语为什么重要？历史高播样本揭示了哪些有效机制，又有哪些不该机械模仿？如果某个候选只差一个词，直接写出替换方案。{highlight_alignment_check}

最后给终审具体可执行的补强方向，每条指向本期真实材料或一个清楚的受众盲区。
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

根据评审补充必要的新标题，但先重新判断评审是否真的理解了材料；不要机械执行一个较弱建议。

然后从所有候选里选出最终标题，通常 5–10 个，质量不足可以更少。比较它们是否有内容证据、是否具体、目标观众能否迅速看懂厉害和稀缺在哪里、是否提供真实的 stakes 或信息余量、以及是否像这个频道而不是通用营销号。问题和结论都可以成立；关键是承诺能被视频兑现。

为排名前 5 的标题各给一条封面建议。每条说明：缩略图主文案、真正增加理解的一项身份／数字小字、应选怎样的人物表情和画面关系，以及 16:9 YouTube 与 3:4 小红书怎样分别排。不要默认三句金句、商务海报或大面积黑底；封面与标题可以分工，也可以为清晰而适度重复。

## 输出文件格式（这是给剪辑师和主播单独阅读的交付文件，不是给你自己的笔记）

固定三段结构，顺序不可变：

1. `## 最终标题`——置顶，按推荐顺序编号。每条带标题文本（标注字数）和一段自我完备的推荐理由，最后给一行投放建议。
2. `## 前 5 标题的封面建议`
3. `## 备选`——每条写出标题完整原文和未进最终的原因。

交付文件不出现 A1、R2-4、“Round 1 指令”等内部轮次代号；读者没有看过工作区。补充候选和填补盲区的过程留在工作文件，交付稿只保留标题、理由和投放判断。
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
        highlight_alignment_check = "\n- 把标题与高光当作组合检查：哪些已经清楚兑现或推进同一承诺，哪些因为重复而变弱，哪些适度重复反而帮助陌生观众理解？不要套用永久的分工公式。"
    else:
        highlights_section = ""
        highlight_alignment_check = ""

    prompt = ROUND1_PROMPT.format(
        guideline=load_guideline(),
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
        highlights_section = f"## 视频高光片段（作为真实素材与整体观看路径参考）\n\n{highlights}\n\n---\n"
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
    highlights_path: Path | None = None,
    discover_highlights: bool = True,
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

    if highlights_path is not None:
        if not highlights_path.is_file():
            raise FileNotFoundError(f"指定的 highlights 不存在: {highlights_path}")
        highlights = highlights_path.read_text(encoding="utf-8")
    elif discover_highlights:
        highlights = find_highlights(content_path, episode_stem, output_dir=out_dir)
    else:
        highlights = ""
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

    candidate_out = workspace / "round2_candidate.md"
    run_round2(r0, r1, highlights, candidate_out)
    candidate_text = candidate_out.read_text(encoding="utf-8")
    raise_for_errors("标题", validate_title_output(candidate_text))
    candidate_out.replace(final_out)
    return final_out


def main() -> None:
    parser = argparse.ArgumentParser(description="课代表立正播客标题三轮生成 v4（高光驱动）")
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
