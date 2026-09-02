#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_titles.py — 课代表立正播客标题工作流 v7

核心变化（相比 v6）：
  - 不再从最稀奇的事实倒推 audience relevance，先找观众原有观看动机与材料独有证据的交点
  - 不用全文概括或“看前 → 看后”统领选题，强段落可以承担整期包装
  - 标题与封面从第一轮就作为同一个信息缺口一起生成
  - 独立 challenger 先避开 brief 和首轮锚定重新选题，再合并候选做可见包装冷测
  - 终稿把 package-specific cold open 或主持人补录 intro 一并设计，不把一般高光当默认开头
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
from tools.speaker_sidecar import find_validated_speaker_srt
from tools.srt_text import cue_timed_text_from_srt, timed_text_from_srt

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


# ── Packaging brief：观众原有观看动机 × 材料独有证据 ──────────────────────

TITLE_BRIEF_PROMPT = """\
你是课代表立正频道的 packaging editor。此刻不要写最终标题，先找到观众原有观看动机与本期独有证据的交点。

## 频道 Guideline

{guideline}

---

{highlights_section}
## 完整源材料

{content}

---

频道的核心发现受众关心科技、进步、AI 与个人成长，很多人在科技公司工作；高管、学生和创业者是自然延伸。嘉宾可能是 VC、研究者或 CEO，但主包装不因此自动服务那个职业的小圈子。

先通读全部材料，辨认它确实碰到的少量观看动机：观众在看到本视频之前，本来就在意、担心或好奇什么？这个动机可以来自自己的选择与未来，也可以来自一个人物、事件或现象本身已有的公共意义。不要写人口画像，也不要替一个本来没人关心的故事事后发明用途。

再把整期当作证据库，扫描能刺穿、加剧或重新定义这些问题的内容：反常事实、具体数字、不合常理的选择、罕见关系、尖锐问题、现场转折、第一手经历、机制，以及能被一口复述的判断。特别检查现场是否已经有人用日常语言说出一句有张力的判断；先保住它的力道，不要把它翻译成更抽象的行业术语。一个强点可以只来自整期中的一段，不需要代表全部内容，但要同时拥有真实观看动机、独特内容证据与充分兑现。

对每个真正有潜力的交点，写清：

- 观众在点开前已经存在的观看动机；
- 材料给出的独有证据、机制或答案，以及可核对的时间点或原文；
- 一句话 premise；
- 拿掉编辑解释后，标题与封面可以打开的具体问题；
- 最简单的封面视觉、视频兑现位置，以及主要风险。

不要为了凑数保留普通角度。一个故事如果只让人觉得“这个人很厉害”，观看动机却要靠额外一段编辑说明来补，它更适合正文、高光或次级包装。最后选出少量兼具原生兴趣、记忆载体与诚实兑现的交点，并说明为什么它们胜过全面摘要和单纯奇观。`看前 → 看后` 只可用来复核观众点进来后是否真的得到东西，不把它当作选题中心，也不要求标题概括整期。

输出一份供后续编辑使用的 packaging brief。不要生成最终标题。
"""


# ── Round 0：把最强交点做成标题 × 封面组合 ──────────────────────────────────

ROUND0_PROMPT = """\
你是课代表立正频道的资深标题编辑。

## 频道 Guideline

{guideline}

---

## 本期 Packaging Brief

{brief}

---

不要给整期写摘要。把 brief 中最强的几个“观众原有观看动机 × 材料独有证据”分别做成标题 × 封面组合；每组只推动一个主问题。标题可以打开情境、因果或矛盾，封面用一个人物、数字或视觉对比让同一个缺口变得可见。两者可以适度重复，但组合后不要提前把答案说完。

观看动机要存在于观众实际看见的标题与封面，编辑理由和第二人称都不能替它补意义。人物、公司、数字与稀缺访问可以增强一个已经成立的问题；它们本身已有公共意义时，也可以成为问题的中心。

每组写出：

- 它承接的观众原有观看动机；
- premise；
- 完整视频标题；
- 封面主文案与一个可执行的视觉关系；
- 只看标题与封面时，观众脑中被打开的那一个问题；
- 视频兑现的时间点／段落，和开头怎样接住它；
- 这组包装最大的风险。

探索少量真正不同的强点，不围着一个全面但平庸的主题做十种同义改写。最后指出最值得 challenger 攻击的组合。
"""


# ── Round 1A：不看首轮的独立候选 ─────────────────────────────────────────────

ROUND1_INDEPENDENT_PROMPT = """\
你是课代表立正频道的独立 packaging challenger。你没有看过本期 Packaging Brief 或 Round 0；请只依据频道判断基准和完整源材料独立选题。

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

找出少量最强交点：核心观众本来就想知道答案，本期又有独有的事实或机制支撑。一个强段落可以承担整期包装；篇幅、信息量或数字大小不决定优先级。

为每个交点提出一组标题 × 封面，并写明观众会自然追问什么、材料在哪里兑现、开头如何接住，以及最可能的误读。只保留在陌生信息流里一秒能读懂、十分钟后还能复述的组合。人物或事件本身若已有足够意义，可以形成真实动机；否则不要强行映射到个人用途。历史样本只校准观众曾为何付出注意力，不模仿句式。
"""


# ── Round 1B：把独立候选与首轮候选放在一起冷测 ──────────────────────────────

ROUND1_COMPARE_PROMPT = """\
你是课代表立正频道的冷启动 challenger。两套候选由彼此独立的编辑生成；现在比较它们，而不是默认任何一套抓对了重点。

## 当前频道判断基准

{guideline}

---

{highlights_section}
## 独立候选

{independent}

---

## Packaging Brief

{brief}

---

## Round 0 候选

{round0}

---

先只看每组标题与缩小后的封面，再读编辑解释。判断观众会自然想知道答案，还是只会觉得人物厉害、事实稀奇；相关性是否需要一段话事后补上；第二人称是否只是在语法上假装亲近。人物或事件本身已有足够意义时，不需要强行改写成个人用途。

同时检查组合是否只打开一个清楚问题、十分钟后能否复述、视频能否具体兑现、开头能否迅速证明观众没有点错。给终审明确的存废和排序；若两轮都错过了更强交点，可以提出新组合。{highlight_alignment_check}
"""


# ── Round 2：补强 + 最终选题 ──────────────────────────────────────────────────

ROUND2_PROMPT = """\
你是课代表立正频道的终审编辑。这是最终决定。

{highlights_section}

{opening_source_section}

## 本期 Packaging Brief

{brief}

---

## Round 0 全部候选

{round0}

## Round 1 评审

{round1}

---

根据评审补充必要的新组合，但先重新判断评审是否真的理解了材料；不要机械执行一个较弱建议。

从所有候选中只选一个明确首选，再保留至多四个真正有不同投放理由的备选。排名看观众原有观看动机与本期独有证据的结合、记忆、视觉清晰与内容兑现，不看对全片的覆盖率，也不看事实本身有多传奇。

标题、封面与开头作为一条连续承诺终审。标题本身要能一口读完、过一会儿仍能复述；能由封面承担的第二项证据就留给封面。封面说明只保留主文案、一个视觉关系和必要的小字。

开头先确认观众从标题与封面带进来的同一个问题，再增加一个新的具体事实、矛盾或后果，随后进入第一段实质内容。不要另造无关 hook，也不要在已经声明“这期会讲什么”之后继续堆履历、背景或 roadmap。对访谈，根据完整材料选择更可靠的一条路：

- 若一至数段原片能独立承担确认、加深与进入答案，给出 package-specific cold open 的准确时间点、顺序与保留原话；
- 若最强 premise 分散在整场、依赖主持人的综合或背景，写一段可直接补录的简短 narrative intro，并指出紧接哪段原片；
- 只有两条路都真实可行、值得实验时，才给一个明确标注的备选。不要把一般高光列表或“最精彩金句合集”当默认开头。

## 输出文件格式（这是给剪辑师和主播单独阅读的交付文件，不是给你自己的笔记）

固定三段结构，顺序不可变：

1. `## 首选组合`——置顶，只放一个选择。依次使用独立字段行 `**标题：**`、`**封面主文案：**`、`**封面画面：**`、`**观众会追问：**`、`**视频兑现：**`、`**开头衔接：**`。`观众会追问` 只写标题与封面真正打开的那一个问题，不写受众说明；兑现尽量给时间点或原文线索。`开头衔接` 下必须另起字段：
   - `**开头类型：** source-cold-open`、`host-narrative` 或 `hybrid`，只写其中一个机器可读值；
   - 每段原片各用一行 `- **原片：** HH:MM:SS,mmm --> HH:MM:SS,mmm｜逐字原话`。in/out 必须来自 cue-level SRT，原话连续照抄，不把 speaker label 抄进引语；
   - 涉及补录时，用一行 `**补录逐字稿：**` 给出可直接录制的完整文字；
   - 最后用一行 `**进入正片：** HH:MM:SS,mmm`。没有时间线材料时只能写 `待定位`，且首选使用 `host-narrative`。
   `开头衔接` 不写松散建议。若逐字稿没有经过 speaker attribution 校验，不得把某段声音擅自标作“主持人”或“嘉宾”，只按时间与原话交付。
2. `## 备选组合`——至多四个，沿用同样字段格式并写明它服务的不同点击动机，不提交同义改写。
3. `## 放弃的方向`——只记录那些看似合理、最终因平庸、受众太窄、视觉不清或兑现不足而放弃的方向。

交付文件不出现 A1、R2-4、“Round 1 指令”、“看前／看后”等内部分析标签；读者没有看过工作区。补充候选和填补盲区的过程留在工作文件，交付稿只保留可直接判断和执行的包装。
"""


OPENING_REPAIR_PROMPT = """\
你是剪辑交付 QC 修复员。下面的标题终稿只有开头交付未通过确定性检查。

## QC 错误

{errors}

## 待修复终稿

{candidate}

## 可核对的 cue-level 逐字稿

{opening_source}

---

保留标题、封面、观众问题、视频兑现、备选与放弃方向；只修复首选组合的 `开头衔接`。严格使用这些字段和值：

- `**开头类型：** source-cold-open`、`host-narrative` 或 `hybrid`
- 每段原片：`- **原片：** HH:MM:SS,mmm --> HH:MM:SS,mmm｜逐字原话`
- 涉及补录：`**补录逐字稿：** 完整可录文字`
- `**进入正片：** HH:MM:SS,mmm`；无时间线时为 `待定位`

原片每行的 in 必须覆盖引语第一个字所在 cue，out 必须覆盖最后一个字所在 cue；引语连续照抄，不补写、不改词、不把 speaker label 抄进引语。{attribution_rule}

输出修复后的完整终稿，不解释修复过程。
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

    print("    Packaging brief：观众原有观看动机 × 材料独有证据…", flush=True)
    call_content_file_based(prompt, out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {out.name} 已写入")
    return out


def run_round0(brief: Path, workspace: Path) -> Path:
    out = workspace / "round0_candidates.md"
    prompt = ROUND0_PROMPT.format(
        guideline=load_guideline(),
        brief=brief.read_text(encoding="utf-8"),
    )

    print("    Round 0：最强交点 → 标题 × 封面组合…", flush=True)
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
    independent_out = workspace / "round1_independent.md"
    review_out = workspace / "round1_review.md"
    r0 = round0.read_text(encoding="utf-8")
    top_titles = load_top_titles()

    if highlights:
        highlights_section = f"## 视频高光片段\n\n{highlights}\n\n---\n"
        highlight_alignment_check = "\n- 把标题、封面、高光和开头当作一条观看路径：高光是否迅速证明这组包装值得相信，还是需要换成更能接住同一强点的片段？"
    else:
        highlights_section = ""
        highlight_alignment_check = ""

    independent_prompt = ROUND1_INDEPENDENT_PROMPT.format(
        guideline=load_guideline(),
        top_titles=top_titles,
        highlights_section=highlights_section,
        content=content,
    )

    print("    Round 1A：独立 challenger 重新读材料…", flush=True)
    call_content_file_based(independent_prompt, independent_out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {independent_out.name} 已写入")

    compare_prompt = ROUND1_COMPARE_PROMPT.format(
        guideline=load_guideline(),
        highlights_section=highlights_section,
        independent=independent_out.read_text(encoding="utf-8"),
        brief=brief.read_text(encoding="utf-8"),
        round0=r0,
        highlight_alignment_check=highlight_alignment_check,
    )

    print("    Round 1B：独立候选 × 首轮候选冷测…", flush=True)
    call_content_file_based(compare_prompt, review_out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {review_out.name} 已写入")
    return review_out


def run_round2(
    brief: Path,
    round0: Path,
    round1: Path,
    highlights: str,
    opening_source: str,
    speaker_attribution_verified: bool,
    final_out: Path,
) -> Path:
    r0 = round0.read_text(encoding="utf-8")
    r1 = round1.read_text(encoding="utf-8")

    if highlights:
        highlights_section = f"## 视频高光片段（作为真实素材与整体观看路径参考）\n\n{highlights}\n\n---\n"
    else:
        highlights_section = ""

    if opening_source:
        attribution_note = (
            "下列 cue 已有经过当前 SRT 校验的 speaker label，可以沿用。"
            if speaker_attribution_verified
            else "下列 cue 没有可靠 speaker label；不要把声音归为主持人或嘉宾。"
        )
        opening_source_section = (
            "## 开头定位用完整时间线逐字稿\n\n"
            f"{attribution_note}\n\n"
            f"{opening_source}\n\n---\n"
        )
    else:
        opening_source_section = (
            "## 开头定位材料\n\n"
            "本次没有提供带时间线逐字稿。首选只能交付 host-narrative，进入正片标为待定位；"
            "不得伪造时间点、原片引语或说话人归因。\n\n---\n"
        )

    prompt = ROUND2_PROMPT.format(
        highlights_section=highlights_section,
        opening_source_section=opening_source_section,
        brief=brief.read_text(encoding="utf-8"),
        round0=r0,
        round1=r1,
    )

    print("    Round 2：补强 + 最终选题…", flush=True)
    call_content_file_based(prompt, final_out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {final_out.name} 已写入")
    return final_out


def repair_round2_opening(
    candidate: Path,
    errors: list[str],
    opening_source: str,
    speaker_attribution_verified: bool,
    repaired_out: Path,
) -> Path:
    attribution_rule = (
        "逐字稿已有可靠 speaker label，可以沿用。"
        if speaker_attribution_verified
        else "逐字稿没有可靠 speaker label，不得把原片声音归为主持人或嘉宾。"
    )
    prompt = OPENING_REPAIR_PROMPT.format(
        errors="\n".join(f"- {error}" for error in errors),
        candidate=candidate.read_text(encoding="utf-8"),
        opening_source=opening_source or "（无时间线材料）",
        attribution_rule=attribution_rule,
    )
    print("    Round 2 QC：修复开头 in/out、原话或结构…", flush=True)
    call_content_file_based(prompt, repaired_out, model=DEFAULT_CONTENT_MODEL)
    print(f"    ✓ {repaired_out.name} 已写入")
    return repaired_out


# ── 主流程 ──────────────────────────────────────────────────────────────────────

def generate_titles(
    content_path: Path,
    stop_at_round: int = 2,
    output_dir: Path | None = None,
    workspace_dir: Path | None = None,
    stem: str | None = None,
    highlights_path: Path | None = None,
    discover_highlights: bool = True,
    source_srt_path: Path | None = None,
    speaker_srt_path: Path | None = None,
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

    resolved_source_srt = source_srt_path
    if resolved_source_srt is None and content_path.suffix.lower() == ".srt":
        resolved_source_srt = content_path
    if resolved_source_srt is not None and not resolved_source_srt.is_file():
        raise FileNotFoundError(f"指定的开头定位 SRT 不存在: {resolved_source_srt}")

    resolved_speaker_srt: Path | None = None
    if resolved_source_srt is not None:
        if speaker_srt_path is not None:
            resolved_speaker_srt = find_validated_speaker_srt(
                resolved_source_srt, [speaker_srt_path]
            )
            if resolved_speaker_srt is None:
                raise ValueError("指定的 speaker SRT 与当前 source SRT 不一致或没有标签")
        else:
            resolved_speaker_srt = find_validated_speaker_srt(
                resolved_source_srt,
                [
                    out_dir / f"{episode_stem}.speaker_labeled.srt",
                    resolved_source_srt.parent / f"{episode_stem}.speaker_labeled.srt",
                    workspace_base / f"{episode_stem}.speaker_labeled.srt",
                ],
            )
    opening_source = (
        cue_timed_text_from_srt(resolved_speaker_srt or resolved_source_srt)
        if resolved_source_srt is not None
        else ""
    )

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
    run_round2(
        brief,
        r0,
        r1,
        highlights,
        opening_source,
        resolved_speaker_srt is not None,
        candidate_out,
    )
    candidate_text = candidate_out.read_text(encoding="utf-8")
    validation_errors = validate_title_output(
        candidate_text,
        resolved_source_srt,
        speaker_attribution_verified=resolved_speaker_srt is not None,
    )
    if validation_errors:
        repaired_out = workspace / "round2_candidate_repaired.md"
        repair_round2_opening(
            candidate_out,
            validation_errors,
            opening_source,
            resolved_speaker_srt is not None,
            repaired_out,
        )
        candidate_out = repaired_out
        candidate_text = candidate_out.read_text(encoding="utf-8")
        validation_errors = validate_title_output(
            candidate_text,
            resolved_source_srt,
            speaker_attribution_verified=resolved_speaker_srt is not None,
        )
    raise_for_errors("标题", validation_errors)
    candidate_out.replace(final_out)
    return final_out


def main() -> None:
    parser = argparse.ArgumentParser(description="课代表立正播客标题生成 v7（观看动机与内容证据驱动）")
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
    parser.add_argument(
        "--source-srt",
        default=None,
        help="可选完整带时间 SRT；输入为文章时用于精确设计原片 cold open",
    )
    parser.add_argument(
        "--speaker-srt",
        default=None,
        help="可选 speaker_labeled.srt；必须与 --source-srt 的 cue 和文字一致",
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
            source_srt_path=Path(args.source_srt).resolve() if args.source_srt else None,
            speaker_srt_path=Path(args.speaker_srt).resolve() if args.speaker_srt else None,
        )
        print(f"  ✓ 标题已写入：{out.name}")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
