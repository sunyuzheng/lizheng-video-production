#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_article.py — SRT 字幕 → 课代表立正风格文章

将精校后的字幕逐字稿整理成结构化文章：识别并放大主播真正有力量的
观点、人格、风格和独特判断，同时让文章更清楚、更锋利、更易读。

用法：
  python3 tools/generate_article.py episode.final.srt
  python3 tools/generate_article.py episode.corrected.srt
  python3 tools/generate_article.py episode.final.srt --max-chars 0

输出：episode.article.md
"""

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.claude_cli import DEFAULT_MODEL, call_claude_file_based


# ── 风格 Prompt（内联说明，不依赖外部文件）─────────────────────────────────────

STYLE_BRIEF = """\
你在为「课代表立正」整理文章。

你要像一个好编辑：找到主播真正想说的东西，帮他说得更有力。不要给文章套模板，不要把口播改成营销号、AI 总结、咨询报告或鸡汤文。

核心任务是三件事：

1. **识别**：逐字稿里什么是真正有力量的？哪个判断是作者独有的、别人说不出来的？哪个场景让抽象观点变得具体可信？哪句话带着这个人的性格？
2. **放大**：把这些有力量的部分移到更显眼的位置，给它们更多空间，让推理链更完整，让读者能跟上作者是怎么得出这个结论的。
3. **清理**：删掉挡在有力量内容前面的杂质：重复、绕弯、铺垫过长、没有信息量的过渡句。清理的目的，是让好东西更突出；文章变短只是可能的结果。

## 写作判断框架

### 找到文章的脊椎
每篇文章都有一个核心判断——作者最想让读者带走的那个东西。在动手改之前，先找到它。如果逐字稿里有多个判断在竞争，选最有力量的那个做主线，其他的降级为支撑或砍掉。

问自己：如果读者只记住一句话，应该是哪句？这句话在文章里出现得够早、够清楚吗？

### 让每一段都在推进
每一段要么提供新的信息（事实、场景、例子），要么推进作者的推理（因为 A 所以 B），要么改变读者的理解（你以为是这样，其实是那样）。如果一段只是在制造气氛、重复已经说过的话、或者让作者显得很厉害，它就是杂质。

### 保护作者的声音和人格
作者的讲法就是他的风格：用哪些词、怎么起句子、节奏是快是慢、幽默还是严肃、喜欢类比还是喜欢直说。改稿时保护这些东西，甚至放大它们。

如果作者习惯用短句推进节奏，不要把它们合并成长句。如果作者有一种直率、不客气的说法，不要替他变得圆滑。你改的是结构和清晰度，不是人格。

### 让判断站住
作者的独特判断是文章最值钱的部分。判断后面要有来路。读者要能看出「他为什么这么想」——是经验、是例子、是推理、还是排除法。

如果逐字稿里有一个真正 disruptive 的 idea——一个改变理解框架的判断——不要埋掉它。把它放到显眼的位置，然后花足够的篇幅解释它为什么成立。

抽象判断要能落地。不要只说「这个思路更高级」，要说明它改变了哪个选择、排除了哪条路、避免了什么后果。

### 禁止元叙述和空转总结
- **元叙述**：指着内容说话而不把内容给出来。「这期的核心命题可以压成一句话：X」「这段值得先看，因为它解决了一个前置问题」「注意这里的证据等级」——直接说 X、直接给证据，让事实自己产生分量；好不好由读者得出，不由作者宣布。
- **空转总结**：「三个商，每一个都被他重新定义过」——说了"有定义"却不给定义。直接写出重新定义是什么、意义是什么。
- 检验法：把句子里指向内容的话删掉，看剩下的还有没有信息量。「值得记下来」「冲击力最强」「最核心的框架」这类话本身零信息：要么删，要么换成内容本身。

### 开头要快
读者没有义务给你耐心。开头尽快让读者知道两件事：这篇文章要讨论什么问题，以及作者有一个值得听的判断。

### 结构服务于理解
小节、框架、分类都是工具，不是装饰。好的结构让读者在任何时候都知道自己在哪、作者要带自己去哪。

小标题要朴素、具体，像是作者在讲下一段内容前说的那句话。框架要从内容中自然长出来，不要硬造。

## 边界

- 不要替作者编内心戏。可以使用自然的第一人称推理语气，但必须有逐字稿支撑。
- 不要贴脸否定具体人。写到某个人错过建议或判断不够好，把否定上移到抽象层面：这类建议容易被错过、这种判断在那个阶段很难做出来、信息不够的时候人会这样选择。
- 尊重读者。不要俯视、羞辱或冒犯。如果讲到认知或行动上的不足，要有同理心。
- 不要 AI 腔：避免用「不是……而是……」做硬转折；不要过度排比、过度罗列、过度总结；不要用「底层逻辑」「抓手」「赋能」「认知跃迁」这类空泛词。
- 不要故弄玄虚。需要表达重要性时，用具体后果、时间尺度和选择成本。避免「改命级别」「改变命运」「宿命」「觉醒」「命运齿轮」这类神秘化词汇，也不要把具体判断夸张成命运叙事。

## 中文自然度检查

输出前做一遍轻量翻译腔检查。很多 AI 味来自「中文词汇 + 英文句法骨架」。不要逐字修补别扭句子；先弄清意思，再用中文里本来会怎么说这件事重新说一遍。

- 检查思考过程里的物理动作动词，例如「接住反馈」「击穿论证」「更锋利的表达」「成本不爆」。不机械禁用，但要确认中文日常里是否真这样搭配。
- 删掉替读者预先下判断的形容词，例如「逻辑很清晰：」「问题很直接：」「更锋利的重构：」。让事实本身产生清楚、直接、有力的感觉。
- 改掉抽象名词做主语、形容词收尾的句子，例如「工程上的现实更难看」。优先让人、动作、具体对象做主语。
- 有稳定中文译法的英文词尽量换成中文；但如果英文词是主播原话、技术圈没有稳定译法，或保留英文更准确，就不要机械替换。要保护主播本来的口语和技术表达。
"""

ARTICLE_INSTRUCTION = """\
根据以下视频逐字稿和高光分析，整理成一篇文章。

请先在心里完成编辑判断：这期最有力量的 insight 是什么？作者独有的判断是什么？哪些故事和表达最能体现作者人格？然后直接输出文章，不要输出分析过程。

先判断视频类型。

如果是访谈、播客、圆桌或多方对谈，文章的默认形态是「视频伴读/解读稿」，不是普通公众号观点文，也不是摘要。它的功能是让观众看完之后，对整期视频的主线、精彩片段、可跳转高光、嘉宾原话、以及这些观点为什么重要，都有清晰理解。写法要像高质量伴读：既给没看视频的人建立完整地图，也让看过视频的人重新抓住最有价值的细节。

访谈稿要求：
- 用第一人称「我」写，默认「我」是课代表立正；客观陈述这场对话，不要替我加内心戏。
- 明确区分主持人、嘉宾和其他发言者。不要把别人的判断写成我的判断。
- 开头要先说明这期真正讨论的问题，以及为什么这期值得看；不要只写嘉宾履历介绍。
- 前半部分必须给出「观看地图」或等价小节：用 8-12 条时间戳告诉观众每个阶段在聊什么、适合谁跳转观看。
- 保留时间戳。重要小节标题或段落开头可以带大致时间，例如「31:00｜大厂打工人的底层梦想，是当好学生」。
- 必须引用原话，尤其是最有冲击力、最能代表嘉宾/主持观点的句子。引用可以轻微清理口语，但不能改变意思。
- 每个高光段落都要完整交代三件事：这里发生了什么，嘉宾/主持的关键原话是什么，为什么值得跳到这里看。
- 每个高光之后要有 synthesize：这句话为什么重要？它改变了我们对什么问题的理解？观众可以怎么用它反思自己的处境、技术判断或职业/创业选择？
- 高光不只选戏剧性片段，还要选能支撑整期主线的片段。如果某段适合单独剪短视频，可以在正文中自然指出，但不要把文章写成剪辑清单。
- 总结可以长，信息密度要高。宁可把关键背景、技术脉络、创业判断和人物选择讲完整，也不要把丰富访谈压成几段泛泛总结。
- 结构通常是：开头说明这期真正讨论的问题；给出整期观看地图；分 6-10 个大段写高光；结尾回到整期的核心价值。

如果是单口视频，文章要像主播本人在状态很好时写出来的版本：更清楚、更锋利、更易读，但不是另一个人写的。可以更书面、更有条理；不要牺牲作者原本的直觉、锋芒和判断力。

单口稿结构建议：
- 开头几段内让读者知道文章讨论的问题和作者的核心判断，不要用「今天我们来聊」。
- 正文按内容自然分成 3-5 个小节。小节要服务理解，不要为了形式整齐而拆。
- 每个重要判断都要让读者看见来路：经验、例子、推理、比较、排除法或具体后果。
- 结尾收束到一个清楚的判断或自我提醒，不要说教，不要喊口号。

字数：单口通常 1200-2500 字；长访谈可以更长，优先把主线、高光原话和解读讲完整，不要把高价值细节压扁。
格式：Markdown，节标题用 ##。小标题要像自然文章标题，不要用生造概念。

只输出文章本身，不要加"以下是文章"之类的前言。
"""


# ── SRT 文本提取 ────────────────────────────────────────────────────────────────

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
    """提取 SRT 文本，并按时间窗口合并，保留可用于文章的粗时间戳。"""
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


# ── 主函数 ──────────────────────────────────────────────────────────────────────

def _episode_stem(path: Path) -> str:
    stem = path.with_suffix("").stem
    for suffix in (".speaker_labeled", ".final", ".corrected", ".qwen"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _read_highlights(srt_path: Path, output_dir: Path, episode_stem: str) -> str:
    candidates = [
        output_dir / f"{episode_stem}.highlights.md",
        srt_path.parent / f"{episode_stem}.highlights.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


def _read_speaker_labeled(srt_path: Path, output_dir: Path, episode_stem: str) -> str:
    candidates = [
        output_dir / f"{episode_stem}.speaker_labeled.md",
        output_dir / f"{episode_stem}.speaker_labeled.srt",
        srt_path.parent / f"{episode_stem}.speaker_labeled.md",
        srt_path.parent / f"{episode_stem}.speaker_labeled.srt",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


def generate_article(
    srt_path: Path,
    max_chars: int = 0,
    output_dir: Path | None = None,
    stem: str | None = None,
) -> Path:
    """SRT → 文章，返回输出文件路径"""
    out_dir = output_dir or srt_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    episode_stem = stem or _episode_stem(srt_path)
    output_path = out_dir / f"{episode_stem}.article.md"

    highlights = _read_highlights(srt_path, out_dir, episode_stem)
    speaker_labeled = _read_speaker_labeled(srt_path, out_dir, episode_stem)
    # 访谈如果有说话人标注稿，直接把它作为主逐字稿，避免重复塞入两份长 transcript。
    text = speaker_labeled if speaker_labeled else srt_to_timed_text(srt_path)
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars] + "…（已截断）"
    transcript_label = "本期说话人标注逐字稿" if speaker_labeled else "本期逐字稿"

    # 构造 prompt
    prompt = (
        STYLE_BRIEF
        + "\n\n"
        + ARTICLE_INSTRUCTION
        + (
            "\n\n以下是本期高光分析，可作为选题、时间戳和原话线索：\n\n---\n"
            + highlights
            + "\n---"
            if highlights
            else "\n\n本期没有提供 highlights.md，请直接从逐字稿中判断主线和高光。"
        )
        + (
            "\n\n以下逐字稿已经带说话人标注。访谈归因以 speaker label 为准：只有明确标成嘉宾/主持人的内容，才可以写成「嘉宾说 / 我问」。UNKNOWN 或 MIXED 段落不得强行归因。"
            if speaker_labeled
            else ""
        )
        + f"\n\n以下是{transcript_label}：\n\n---\n"
        + text
        + "\n---"
    )

    call_claude_file_based(prompt, output_path, model=DEFAULT_MODEL)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="SRT 字幕 → 课代表立正风格文章")
    parser.add_argument("srt", help="输入 SRT 文件路径（.final.srt 或 .corrected.srt）")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=0,
        help="逐字稿截断长度；0 表示不截断（默认 0）",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help="输出目录（默认与输入文件同目录）",
    )
    args = parser.parse_args()

    srt_path = Path(args.srt).resolve()
    if not srt_path.exists():
        print(f"错误: 文件不存在: {srt_path}")
        sys.exit(1)

    print(f"  生成文章：{srt_path.name} …", flush=True)
    try:
        out = generate_article(
            srt_path,
            max_chars=args.max_chars,
            output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
        )
        print(f"  ✓ 文章已写入：{out.name}")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
