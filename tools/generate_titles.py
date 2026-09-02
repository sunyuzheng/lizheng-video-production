#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_titles.py — 课代表立正播客标题工作流 v5

核心变化（相比 v4）：
  - 先从完整材料建立“观众看前 → 看后”的标题 brief，再生成候选
  - 嘉宾履历、知名公司和数字先作为答案可信度的证据，不自动充当观众 stakes
  - challenger 重新读取源材料并独立寻找观众问题，不围着第一轮做同义改写
  - 历史高播标题用于校准需求强度，不作为句式模板
  - 默认使用 Codex CLI，失败时降级 Claude；模型由 CLI 默认或环境变量选择

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

from tools.content_cli import DEFAULT_CONTENT_MODEL, call_content_file_based
from tools.asset_qc import raise_for_errors, validate_title_output
from tools.srt_text import timed_text_from_srt

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

def srt_to_text(srt_path: Path) -> str:
    """Return the complete timeline-aware transcript for title analysis."""
    return timed_text_from_srt(srt_path, window_seconds=60)


# ── Title brief：完整材料 → 观众认知转变 ───────────────────────────────────────

TITLE_BRIEF_PROMPT = """\
你是课代表立正频道的选题编辑。此刻不要写标题，先找出观众为什么值得花时间看完这期。

## 频道 Guideline

{guideline}

---

{highlights_section}
## 完整源材料

{content}

---

先恢复本期真正回答的问题，不要把“出现了哪些公司／人物／概念”当作答案。频道的多数观众未必从事嘉宾的职业，但往往正在处理 AI、职业、创业、商业与个人选择中的真实不确定性。题材可以专业，观众的 stakes 需要真实可迁移。

建立几条有证据的 `看前 → 看后`：

- 哪一类具体处境中的观众会在意；
- 他看前用什么直觉、简化或错误问题做判断；
- 哪个矛盾、代价或正在逼近的决定使这个理解不够用；
- 看后能多做出哪个明确区分，或重新判断哪件事；
- 视频中哪些相隔开的事实、推理和案例共同兑现这项转变。

同时区分两层需求：一层是频道较广的发现受众能迁移到自己选择上的问题；另一层是嘉宾同行或特定专业人群更直接、更痛的问题。明确哪一层应拥有完整节目的主标题，以及为什么；不要因为某个垂直段落解释最完整，就自动把整期包装给那一小群人。

然后找出其中最值得大众观众立即补上的一个问题。它不能只靠嘉宾厉害或公司知名产生重要性；人物与履历应解释“为什么相信这个答案”。也列出本期最诱人却较弱的角度，逐一回答它们为什么经不起 `这跟观众有什么关系？`。

输出一份供后续编辑使用的完整 brief，保留可核对的时间点或原文线索。不要生成标题。
"""


# ── Round 0：由观众转变生成候选 ───────────────────────────────────────────────

ROUND0_PROMPT = """\
你是课代表立正频道的资深标题编辑。

## 频道 Guideline

{guideline}

---

## 本期标题 Brief

{brief}

---

标题不是内容摘要，而是对一次有价值的理解变化做出承诺。从 brief 中选择真正重要的 `看前 → 看后`，再写候选。问题、结论、场景、人物身份、数字与冲突都可以使用，不按类型凑数量。

每个方向先在编辑说明里回答：观众看前相信什么，看后能判断什么；为什么现在不补上这个缺口会继续付出代价；视频靠什么具体材料兑现。随后再给出标题，让标题使用观众能一秒读懂的语言。名人、公司与履历可以增加可信度或稀缺性，但不要让“这个人很厉害”冒充观看理由。

候选宁可探索少数真正不同的需求，也不要围着同一个事实做十种措辞。最后选出最值得 challenger 攻击的 3–5 个方向，并写清它们各自最可能失败在哪里。
"""


# ── Round 1：外部基准对比 + 差距分析 ──────────────────────────────────────────

ROUND1_PROMPT = """\
你是课代表立正频道的 challenger。你的任务不是润色第一轮，而是站在陌生观众一边，推翻没有真实观看需求的角度。

## 当前频道判断基准

{guideline}

---

## 频道真实高播标题（用来校准判断）

{top_titles}

---

{highlights_section}

## 完整源材料（请重新阅读，不要默认 Brief 或 Round 0 抓对了重点）

{content}

---

## 本期标题 Brief

{brief}

---

## Round 0 候选标题

{round0}

---

先不看 Round 0 的结论，依据源材料独立写出：这期最值得目标观众解决的一个问题是什么，看前与看后的判断究竟怎样改变。再检查 Brief 是否错把有趣事实当成重要问题，或把嘉宾的职业兴趣误当成大众需求。

随后逐一挑战强候选：陌生观众看到后会追问什么 `so what`？他期待看完弄明白什么？这个答案会不会影响一个真实判断或选择？把嘉宾和知名公司名暂时拿掉，需求是否仍然成立？视频是否用足够篇幅和具体材料兑现，而不只是碰巧说到？

再比较受众优先级：这是完整节目面向频道发现流的主问题，还是只对某个专业子群最痛的切片问题？“对少数人很痛”和“对更多人有真实利害”都可以成立，但不要把前者因为材料集中、措辞具体就自动排第一，也不要用虚假的普遍化抬高后者。

历史高播样本只用于观察频道观众曾对哪些问题付出过注意力，不模仿句式。不要用“更有冲击力”“更具体”这类空评价。如果第一轮选错问题，直接废掉并提出新的需求与标题；如果只差一个词，再给替换方案。{highlight_alignment_check}

最后交给终审一份明确的存废意见，以及你独立提出的最强新候选。
"""


# ── Round 2：补强 + 最终选题 ──────────────────────────────────────────────────

ROUND2_PROMPT = """\
你是课代表立正频道的终审编辑。这是最终决定。

{highlights_section}

## 本期标题 Brief

{brief}

---

## Round 0 全部候选

{round0}

## Round 1 评审

{round1}

---

根据评审补充必要的新标题，但先重新判断评审是否真的理解了材料；不要机械执行一个较弱建议。

然后从所有候选里选出最终标题，通常 5–10 个，质量不足可以更少。第一名优先选择那条能让足够多的目标观众立刻认出一个重要缺口、又能由本期独特材料充分回答的标题。用户没有给出垂直投放目标时，完整节目默认先服务频道较广的发现受众；专业子群的高痛点标题作为有明确投放用途的备选。终审时在内部明确比较每条的 `看前 → 看后`，而不是只夸“有冲击力”；检查公司与身份是在证明答案，还是在冒充 stakes。问题和结论都可以成立，关键是观众能预期一次具体而可信的理解变化。

为排名前 5 的标题各给一条封面建议。每条说明：缩略图主文案、真正增加理解的一项身份／数字小字、应选怎样的人物表情和画面关系，以及 16:9 YouTube 与 3:4 小红书怎样分别排。不要默认三句金句、商务海报或大面积黑底；封面与标题可以分工，也可以为清晰而适度重复。

## 输出文件格式（这是给剪辑师和主播单独阅读的交付文件，不是给你自己的笔记）

固定三段结构，顺序不可变：

1. `## 最终标题`——置顶，按推荐顺序编号。每条带标题文本（标注字数）、不超过两句的推荐理由和一行投放建议。理由直接说明观众问题与内容兑现，不要把内部的“看前／看后”分析标签抄进交付稿。
2. `## 前 5 标题的封面建议`——每条只保留缩略图主文案、确实增加理解的一项身份／数字小字和人物情绪；用户没有要求实际做封面时，不展开版式设计说明。
3. `## 备选`——每条写出标题完整原文和未进最终的原因。

交付文件不出现 A1、R2-4、“Round 1 指令”等内部轮次代号；读者没有看过工作区。补充候选和填补盲区的过程留在工作文件，交付稿只保留标题、理由和投放判断。
"""


# ── 工作流 ───────────────────────────────────────────────────────────────────────

def build_title_brief(content: str, highlights: str, workspace: Path) -> Path:
    out = workspace / "title_brief.md"
    if highlights:
        highlights_section = f"## 视频高光片段\n\n{highlights}\n\n---\n\n"
    else:
        highlights_section = ""

    prompt = TITLE_BRIEF_PROMPT.format(
        guideline=load_guideline(),
        highlights_section=highlights_section,
        content=content,
    )

    print("    Title brief：完整材料 → 观众认知转变…", flush=True)
    call_content_file_based(prompt, out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {out.name} 已写入")
    return out


def run_round0(brief: Path, workspace: Path) -> Path:
    out = workspace / "round0_candidates.md"
    prompt = ROUND0_PROMPT.format(
        guideline=load_guideline(),
        brief=brief.read_text(encoding="utf-8"),
    )

    print("    Round 0：由观众转变生成候选…", flush=True)
    call_content_file_based(prompt, out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {out.name} 已写入")
    return out


def run_round1(
    content: str,
    brief: Path,
    round0: Path,
    highlights: str,
    workspace: Path,
) -> Path:
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
        content=content,
        brief=brief.read_text(encoding="utf-8"),
        round0=r0,
        highlight_alignment_check=highlight_alignment_check,
    )

    print("    Round 1：冷观众 challenger 重新读材料…", flush=True)
    call_content_file_based(prompt, out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {out.name} 已写入")
    return out


def run_round2(
    brief: Path,
    round0: Path,
    round1: Path,
    highlights: str,
    final_out: Path,
) -> Path:
    r0 = round0.read_text(encoding="utf-8")
    r1 = round1.read_text(encoding="utf-8")

    if highlights:
        highlights_section = f"## 视频高光片段（作为真实素材与整体观看路径参考）\n\n{highlights}\n\n---\n"
    else:
        highlights_section = ""

    prompt = ROUND2_PROMPT.format(
        highlights_section=highlights_section,
        brief=brief.read_text(encoding="utf-8"),
        round0=r0,
        round1=r1,
    )

    print("    Round 2：补强 + 最终选题…", flush=True)
    call_content_file_based(prompt, final_out, model=DEFAULT_CONTENT_MODEL)
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

    # 标题 brief 与 challenger 都需要完整材料；只读开头会让后续轮次
    # 围绕一个偶然锚点反复润色，无法发现视频后半段更重要的问题。
    if content_path.suffix == ".md":
        content = content_path.read_text(encoding="utf-8")
    else:
        content = srt_to_text(content_path)

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

    brief = build_title_brief(content, highlights, workspace)
    r0 = run_round0(brief, workspace)
    if stop_at_round == 0:
        return r0

    r1 = run_round1(content, brief, r0, highlights, workspace)
    if stop_at_round == 1:
        return r1

    candidate_out = workspace / "round2_candidate.md"
    run_round2(brief, r0, r1, highlights, candidate_out)
    candidate_text = candidate_out.read_text(encoding="utf-8")
    raise_for_errors("标题", validate_title_output(candidate_text))
    candidate_out.replace(final_out)
    return final_out


def main() -> None:
    parser = argparse.ArgumentParser(description="课代表立正播客标题生成 v5（观众认知转变驱动）")
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
        help="标题过程文件目录（默认与最终标题同目录）",
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
