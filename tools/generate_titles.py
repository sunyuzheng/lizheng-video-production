#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_titles.py — 课代表立正播客标题工作流 v6

核心变化（相比 v5）：
  - 不再用全文概括或“看前 → 看后”统领选题，先从完整材料寻找最强可包装点
  - 标题与封面从第一轮就作为同一个信息缺口一起生成
  - challenger 重新读取源材料，做核心受众、记忆、视觉和兑现的冷启动测试
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


# ── Packaging brief：完整材料 → 可包装强点 ────────────────────────────────────

TITLE_BRIEF_PROMPT = """\
你是课代表立正频道的 packaging editor。此刻不要写最终标题，先从整期内容里找到真正值得包装的强点。

## 频道 Guideline

{guideline}

---

{highlights_section}
## 完整源材料

{content}

---

频道的核心发现受众关心科技、进步、AI 与个人成长，很多人在科技公司工作；高管、学生和创业者是自然延伸。嘉宾可能是 VC、研究者或 CEO，但主包装不因此自动服务那个职业的小圈子。垂直内容可以提供稀缺视角，入口要让核心观众马上感到好奇、重要或与自己的未来有关。

通读全部材料，把它当作可包装强点的素材库，而不是等待你概括的文章。广泛扫描这些可能性：反常事实、具体数字、不合常理的选择、罕见关系、尖锐问题、现场转折、第一手经历、能被一口复述的判断。特别检查现场是否已经有人用日常语言说出一句有张力的反常判断；先保住它的力道，不要把它翻译成更抽象的行业术语。一个强点可以只来自整期中的一段；不需要代表全部内容，但视频要能把它讲透或把它自然扩展成更大的问题。

对每个真正有潜力的强点，写清：

- 一句话 premise，以及可核对的时间点或原文；
- 核心观众看到后会在脑中补出哪一个问题；
- 为什么这个问题重要到不想错过，而不只是一个小冷知识；
- 最简单的封面视觉是什么：人物关系、数字、动作、物件或对比；
- 视频在哪里提供答案、证据或推演，现有开头／高光是否能尽快接住它；
- 它最容易滑向哪一种误导、圈内自嗨或正确但平庸的表达。

不要为了凑数保留普通角度。最后选出少量最有拉力、最容易记住、又能诚实兑现的强点，并说明为什么它们胜过“全面介绍本期内容”的标题。`看前 → 看后` 只可用来复核观众点进来后是否真的得到东西，不把它当作选题中心，也不要求标题概括整期。

输出一份供后续编辑使用的 packaging brief。不要生成最终标题。
"""


# ── Round 0：把强点做成标题 × 封面组合 ───────────────────────────────────────

ROUND0_PROMPT = """\
你是课代表立正频道的资深标题编辑。

## 频道 Guideline

{guideline}

---

## 本期 Packaging Brief

{brief}

---

不要给整期写摘要。把 brief 中最强的几个 premise 分别做成标题 × 封面组合；每组只推动一个主问题。标题可以打开情境、因果或矛盾，封面用一个人物关系、数字或视觉对比让同一个缺口变得可见。两者可以适度重复，但组合后不要提前把答案说完。

优先寻找“熟悉的关切 + 意外的事实”：让科技、AI、进步或个人成长受众马上认得这与自己有关，又因材料里的具体异常而停下来。人物、公司、数字与稀缺访问有时负责证明答案，有时本身就是强点；不要预设它们只能放小字，也不要把履历堆叠当作故事。

每组写出：

- premise；
- 完整视频标题；
- 封面主文案与一个可执行的视觉关系；
- 观众脑中被打开的那一个问题，以及为什么值得现在回答；
- 视频兑现的时间点／段落，和开头怎样接住它；
- 这组包装最大的风险。

探索少量真正不同的强点，不围着一个全面但平庸的主题做十种同义改写。最后指出最值得 challenger 攻击的组合。
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

## 本期 Packaging Brief

{brief}

---

## Round 0 标题 × 封面组合

{round0}

---

先遮住 Round 0 的排名，依据完整源材料独立找出最能被人记住、最想立刻知道答案的三个强点。不要寻找覆盖面最大的主题，也不要默认篇幅最长的段落最适合包装。

随后把每一组候选放进陌生信息流做冷启动测试：

- 一秒后，核心观众脑中出现的是哪一个清楚问题？
- 这个缺口真的重要，还是不知道也无所谓的小悬念？
- 十分钟后，观众还能不能把这个点讲给别人？
- 标题与缩小后的封面是否只呈现一个视觉故事，封面有没有意外把答案揭掉？
- 它首先打中关心科技、进步、AI 与个人成长的人，还是只对 VC 等小职业圈成立？
- 视频在什么位置提供足够具体的兑现；开头有没有很快证明观众没点错？

一个只覆盖强段落、但拉力大且兑现好的组合，可以胜过完整概括。一个全面、正确、无法记住的标题则应直接淘汰。历史高播样本只用来观察频道观众曾为什么付出注意力，不模仿句式。若第一轮错过了最强点，直接提出新的标题 × 封面组合；不要只换刺激词。{highlight_alignment_check}

最后交给终审明确的存废意见、独立新候选，以及需要通过开场剪辑或高光补上的兑现关系。
"""


# ── Round 2：补强 + 最终选题 ──────────────────────────────────────────────────

ROUND2_PROMPT = """\
你是课代表立正频道的终审编辑。这是最终决定。

{highlights_section}

## 本期 Packaging Brief

{brief}

---

## Round 0 全部候选

{round0}

## Round 1 评审

{round1}

---

根据评审补充必要的新组合，但先重新判断评审是否真的理解了材料；不要机械执行一个较弱建议。

从所有候选中只选一个明确首选，再保留至多四个真正有不同投放理由的备选。排名看的是核心观众的拉力、记忆、视觉清晰与内容兑现，不是对全片的覆盖率。嘉宾是 VC 时，不要把 VC 从业者当默认受众；但若一段投资故事里的具体异常能击中更广的科技、成长或选择问题，它完全可以拥有主标题。

标题与封面作为不可拆开的组合终审。标题本身要能一口读完、过一会儿仍能复述；不要把 premise、数字证明、人物身份和两层结论全塞进一句话，能由封面承担的第二项证据就留给封面。封面说明只保留主文案、一个视觉关系和必要的小字；不要默认三句金句、商务海报或大面积黑底。检查现有开头是否会迅速接住封面承诺；若不会，给出一句具体的冷开场／高光调整，不靠标题独自承担完播率。

## 输出文件格式（这是给剪辑师和主播单独阅读的交付文件，不是给你自己的笔记）

固定三段结构，顺序不可变：

1. `## 首选组合`——置顶，只放一个选择。依次使用独立字段行 `**标题：**`、`**封面主文案：**`、`**封面画面：**`、`**观众会追问：**`、`**视频兑现：**`、`**开头衔接：**`。理由保持简短具体；兑现尽量给时间点或原文线索。
2. `## 备选组合`——至多四个，沿用同样字段格式并写明它服务的不同点击动机，不提交同义改写。
3. `## 放弃的方向`——只记录那些看似合理、最终因平庸、受众太窄、视觉不清或兑现不足而放弃的方向。

交付文件不出现 A1、R2-4、“Round 1 指令”、“看前／看后”等内部分析标签；读者没有看过工作区。补充候选和填补盲区的过程留在工作文件，交付稿只保留可直接判断和执行的包装。
"""


# ── 工作流 ───────────────────────────────────────────────────────────────────────

def build_title_brief(content: str, highlights: str, workspace: Path) -> Path:
    out = workspace / "packaging_brief.md"
    if highlights:
        highlights_section = f"## 视频高光片段\n\n{highlights}\n\n---\n\n"
    else:
        highlights_section = ""

    prompt = TITLE_BRIEF_PROMPT.format(
        guideline=load_guideline(),
        highlights_section=highlights_section,
        content=content,
    )

    print("    Packaging brief：完整材料 → 可包装强点…", flush=True)
    call_content_file_based(prompt, out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {out.name} 已写入")
    return out


def run_round0(brief: Path, workspace: Path) -> Path:
    out = workspace / "round0_candidates.md"
    prompt = ROUND0_PROMPT.format(
        guideline=load_guideline(),
        brief=brief.read_text(encoding="utf-8"),
    )

    print("    Round 0：强点 → 标题 × 封面组合…", flush=True)
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
        highlight_alignment_check = "\n- 把标题、封面、高光和开头当作一条观看路径：高光是否迅速证明这组包装值得相信，还是需要换成更能接住同一强点的片段？"
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
    parser = argparse.ArgumentParser(description="课代表立正播客标题生成 v6（强点与标题封面组合驱动）")
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
