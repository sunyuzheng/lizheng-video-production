#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_article.py — SRT 字幕 → 课代表立正风格文章

将精校后的字幕逐字稿整理成结构化文章：识别并放大主播真正有力量的
观点、人格、风格和独特判断，同时让文章更清楚、更锋利、更易读。

用法：
  python3 tools/generate_article.py episode.final.srt --article-type interview
  python3 tools/generate_article.py episode.corrected.srt --article-type monologue
  python3 tools/generate_article.py episode.final.srt --article-type interview --surface companion

输出：episode.article.md
"""

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.content_cli import DEFAULT_CONTENT_MODEL, call_content_file_based
from tools.atomic_delivery import commit_prepared_files
from tools.speaker_sidecar import load_validated_speaker_srt
from tools.srt_text import timed_text_from_srt


@dataclass(frozen=True)
class WritingSkillSpec:
    name: str
    label: str
    bundled_path: Path


@dataclass(frozen=True)
class ResolvedWritingSkill:
    name: str
    label: str
    path: Path
    source: str
    content: str
    sha256: str

    @property
    def prompt_context(self) -> str:
        return (
            f"### {self.label}\n\n{self.content}\n\n"
            "### 自动流水线的 reference 边界\n\n"
            "本次只注入了上面的 SKILL.md 主文件，没有加载其中按需引用的外部文件。"
            "只使用已经出现的原则和本期素材，不推测未提供 reference 的内容。"
        )


_WRITING_SKILLS = {
    "interview": WritingSkillSpec(
        name="expert-interview-article",
        label="访谈文章主责：expert-interview-article",
        bundled_path=(
            _REPO_ROOT
            / "data"
            / "writing-skills"
            / "expert-interview-article.md"
        ),
    ),
    "monologue": WritingSkillSpec(
        name="substance-writing-review",
        label="单口文章主责：substance-writing-review",
        bundled_path=(
            _REPO_ROOT
            / "data"
            / "writing-skills"
            / "substance-writing-review.md"
        ),
    ),
}

_SURFACE_GUIDANCE = {
    "article": "独立文章：围绕问题与推理成立，时间戳只用于内部核对，成稿不需要观看地图。",
    "community": "独立社区帖：开头高密度交付核心 substance，再按社区读者的理解需要展开；除非帖子同时承担活动回放，成稿不需要观看地图。",
    "companion": "视频伴读／活动回放：先 surface 核心 insights，再提供足够但不过密的时间戳导航，让读者能按问题跳看。",
    "release": "发布介绍：用较短篇幅交代最值得点开的事实、问题与人物信息，不把它扩写成完整长文。",
}


def _writing_skill_candidates(spec: WritingSkillSpec) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    override_root = os.environ.get("LIZHENG_WRITING_SKILLS_DIR")
    if override_root:
        candidates.append(
            (
                "environment",
                Path(override_root).expanduser() / spec.name / "SKILL.md",
            )
        )
    candidates.extend(
        [
            ("codex", Path.home() / ".codex" / "skills" / spec.name / "SKILL.md"),
            ("claude", Path.home() / ".claude" / "skills" / spec.name / "SKILL.md"),
            ("bundled", spec.bundled_path),
        ]
    )
    return candidates


def resolve_writing_skill(
    article_type: str,
    explicit_path: Path | None = None,
) -> ResolvedWritingSkill:
    """优先采用显式快照或本机当前 skill；fresh clone 使用仓内 fallback。"""
    if article_type not in _WRITING_SKILLS:
        raise ValueError(f"未知 article_type: {article_type}")
    spec = _WRITING_SKILLS[article_type]
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise FileNotFoundError(f"指定的 writing skill 不存在: {explicit_path}")
        raw = explicit_path.read_bytes()
        actual_name = _skill_name_from_content(raw.decode("utf-8"))
        if actual_name != spec.name:
            found = actual_name or "缺少 YAML frontmatter name"
            raise ValueError(
                f"writing skill 类型不匹配：{article_type} 需要 {spec.name}，"
                f"但 {explicit_path} 是 {found}"
            )
        return ResolvedWritingSkill(
            name=spec.name,
            label=spec.label,
            path=explicit_path,
            source="explicit",
            content=raw.decode("utf-8"),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    attempted: list[str] = []
    for source, path in _writing_skill_candidates(spec):
        attempted.append(str(path))
        if not path.is_file():
            continue
        raw = path.read_bytes()
        return ResolvedWritingSkill(
            name=spec.name,
            label=spec.label,
            path=path,
            source=source,
            content=raw.decode("utf-8"),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    raise FileNotFoundError(
        "缺少主责 writing skill；已检查：" + ", ".join(attempted)
    )


def _skill_name_from_content(content: str) -> str | None:
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", content, re.S)
    if not frontmatter:
        return None
    name = re.search(
        r"^name:\s*['\"]?([a-z0-9-]+)['\"]?\s*$",
        frontmatter.group(1),
        re.M,
    )
    return name.group(1) if name else None


def load_writing_skill_context(article_type: str) -> str:
    """只注入当前类型的一个主责 writing skill。"""
    return resolve_writing_skill(article_type).prompt_context


def resolve_article_type(
    requested: str,
    highlights: str,
    speaker_labeled: str,
    guest_profile: str,
) -> tuple[str, str]:
    """解析单口/访谈；auto 只采用可追溯信号，无法判断时要求显式输入。"""
    if requested != "auto":
        return requested, "explicit"
    if speaker_labeled:
        return "interview", "speaker_labeled"
    if guest_profile:
        return "interview", "guest_profile"

    head = highlights[:2500]
    type_anchor = r"(?:视频类型|内容类型|类型判断|视频类型和主发言人)"
    interview = re.search(
        type_anchor + r"[\s\S]{0,160}(?:访谈|播客|对谈|圆桌)", head, re.I
    )
    monologue = re.search(
        type_anchor + r"[\s\S]{0,160}(?:单口|口播|独白)", head, re.I
    )
    if bool(interview) != bool(monologue):
        return ("interview", "highlights") if interview else ("monologue", "highlights")

    raise ValueError(
        "无法从 speaker labels、guest profile 或 highlights 确认视频类型；"
        "请传 --article-type interview 或 --article-type monologue。"
    )


def resolve_surface(requested: str, article_type: str) -> str:
    if requested == "auto":
        return "companion" if article_type == "interview" else "article"
    return requested


# ── 频道执行 Prompt；选材与结构由运行时加载的主责 writing skill 决定 ──────

STYLE_BRIEF = """\
你在为「课代表立正」整理文章。以判型后的主责 writing skill 完成编辑判断，把成稿写成作者状态很好时亲自写出的版本。保护原材料里的具体事实、原话、节奏和直率程度，清理重复与无信息铺垫，用自然中文表达；不要把内容改成匿名 AI 总结、咨询报告或营销号文章。
"""

ARTICLE_SYSTEM_BRIEF = """\
## 视频文章的核心要求

文章类型与发布 surface 已在下方明确。只采用对应的主责 writing skill。文章要尽快 surface 读者真正值得理解的事实、机制、判断和人物选择，而非依次复述视频话题。

长素材的文章主线有时并不等于最醒目的话题，也没有一句现成金句；它可能分散在相隔很远的事实、选择、推理转折和后续修正里。先把这种观察当作编辑假设，用相隔开的具体片段验证，也寻找会削弱它的事实。最后写出的判断要比标签更窄，能解释一个具体后果，并保留素材中真实的行动、反应和修正。

逐字稿时间戳始终用于证据定位；是否在成稿提供观看导航由发布 surface 决定。只有当事情怎样发生本身很重要时才按时间推进；当读者更需要看懂一个跨段模式时，正文按问题或 insight 组织。无论采用哪种结构，都用具体事实、场景、原话和机制推进，不用通用大词替代分析。
"""

ARTICLE_BRIEF_INSTRUCTION = """\
你是课代表立正频道的资深文章编辑。先不要写正文，先做一份 article brief，用它记录编辑判断，而非逐段摘要或预制目录。

它需要让下一位写作者看清：这篇文章给读者什么阅读承诺；哪些具体事实、机制与原话真正有信息增量；是否存在一条分散在多个片段、没有被任何一句话完整说出的判断；哪些片段支持它，哪些反证会限制它；素材里的人怎样回应反例、修正问题或推进判断；哪些时间戳能定位这些证据。

如果编辑观察成立，就把宽泛直觉收窄成能解释后果的判断。如果证据不足或反证更强，明确放弃它。对每个跨段判断写清 supporting timestamps、counterevidence，以及采用／放弃后的准确措辞。高光文件只提供线索，不决定文章脊椎。按本期材料最自然的结构输出 brief，最后单列事实、归因和隐私风险。只输出 brief，不要写正文。
"""

ARTICLE_INSTRUCTION = """\
根据以下本期素材和 article brief 写出正文。选材、结构、人物线、专业线、作者声音与篇幅由当前唯一主责 writing skill 判断；发布形态由本次 surface 契约决定，不另套一份固定的访谈或单口模板。

article brief 只提供待复核的编辑判断。逐字稿、明确的 speaker labels 和有来源的辅助资料仍是事实源；不要新增材料没有支持的经历、动机、因果或人物定性。各个说话者与资料来源的判断要分清；引用可清理口语，但不改变命题、语气与归因。

写作质量与最终重读方式由当前唯一主责 writing skill 决定；视频流水线不再叠加第二套通用文章规范。

时间戳用于核对证据，只有 `companion` 默认把它们写成观看导航。按当前 surface 直接交付完整 Markdown 成稿，不输出分析过程或“以下是文章”之类的前言。
"""

_TYPE_EDITORIAL_FOCUS = {
    "interview": """\
本期是访谈。查证跨段判断时，重点看采访者的几次追问、嘉宾面对不同问题时重复出现的选择，以及嘉宾如何理解、修正或继续推演。严格区分主持人、嘉宾和其他发言者的判断。
""",
    "monologue": """\
本期是单口。查证跨段判断时，重点看作者在不同例子里是否使用同一套推理，怎样处理反例、修正说法或改变选择。不要因为逐字稿只有一个声音，就把未经检验的主张当成事实。
""",
}


def srt_to_timed_text(srt_path: Path, window_seconds: int = 60) -> str:
    """严格解析 SRT，并按时间窗口保留文章核对用时间戳。"""
    return timed_text_from_srt(srt_path, window_seconds=window_seconds)


# ── 主函数 ──────────────────────────────────────────────────────────────────────

def _episode_stem(path: Path) -> str:
    stem = path.with_suffix("").stem
    for suffix in (".speaker_labeled", ".final", ".corrected", ".qwen"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _read_highlights(
    srt_path: Path,
    output_dir: Path,
    episode_stem: str,
    workspace_dir: Path | None = None,
    highlights_path: Path | None = None,
    discover_highlights: bool = True,
) -> str:
    resolved = _resolve_highlights_path(
        srt_path,
        output_dir,
        episode_stem,
        workspace_dir=workspace_dir,
        highlights_path=highlights_path,
        discover_highlights=discover_highlights,
    )
    return resolved.read_text(encoding="utf-8") if resolved else ""


def _resolve_highlights_path(
    srt_path: Path,
    output_dir: Path,
    episode_stem: str,
    workspace_dir: Path | None = None,
    highlights_path: Path | None = None,
    discover_highlights: bool = True,
) -> Path | None:
    if highlights_path is not None:
        if not highlights_path.is_file():
            raise FileNotFoundError(f"指定的 highlights 不存在: {highlights_path}")
        return highlights_path
    if not discover_highlights:
        return None

    # 交付区是本次 pipeline 刚生成结果的默认位置；工作区只能作最后 fallback，
    # 避免残留的旧 highlights 静默覆盖新产物。
    candidates = [
        output_dir / f"{episode_stem}.highlights.md",
        srt_path.parent / f"{episode_stem}.highlights.md",
    ]
    if workspace_dir:
        candidates.append(workspace_dir / f"{episode_stem}.highlights.md")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _read_speaker_labeled(
    srt_path: Path,
    output_dir: Path,
    episode_stem: str,
    workspace_dir: Path | None = None,
) -> str:
    candidates = [
        output_dir / f"{episode_stem}.speaker_labeled.srt",
        srt_path.parent / f"{episode_stem}.speaker_labeled.srt",
    ]
    if workspace_dir:
        candidates.extend([
            workspace_dir / f"{episode_stem}.speaker_labeled.srt",
        ])
    return load_validated_speaker_srt(srt_path, candidates)


def _read_guest_profile(
    srt_path: Path,
    output_dir: Path,
    episode_stem: str,
    workspace_dir: Path | None = None,
) -> str:
    candidates = []
    if workspace_dir:
        candidates.append(workspace_dir / f"{episode_stem}.guest-profile.md")
    candidates.extend([
        output_dir / f"{episode_stem}.guest-profile.md",
        srt_path.parent / f"{episode_stem}.guest-profile.md",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


def _read_editorial_notes(
    srt_path: Path,
    output_dir: Path,
    episode_stem: str,
    workspace_dir: Path | None = None,
) -> str:
    candidates = []
    if workspace_dir:
        candidates.append(workspace_dir / f"{episode_stem}.editorial-notes.md")
    candidates.extend([
        output_dir / f"{episode_stem}_process" / f"{episode_stem}.editorial-notes.md",
        output_dir / f"{episode_stem}.editorial-notes.md",
        srt_path.parent / f"{episode_stem}_process" / f"{episode_stem}.editorial-notes.md",
        srt_path.parent / f"{episode_stem}.editorial-notes.md",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


def generate_article(
    srt_path: Path,
    max_chars: int = 0,
    output_dir: Path | None = None,
    stem: str | None = None,
    *,
    workspace_dir: Path | None = None,
    article_type: str = "auto",
    surface: str = "auto",
    highlights_path: Path | None = None,
    discover_highlights: bool = True,
    writing_skill_path: Path | None = None,
) -> Path:
    """SRT → 文章，返回输出文件路径"""
    out_dir = output_dir or srt_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    episode_stem = stem or _episode_stem(srt_path)
    output_path = out_dir / f"{episode_stem}.article.md"
    process_dir = workspace_dir or out_dir / f"{episode_stem}_process"
    process_dir.mkdir(parents=True, exist_ok=True)
    brief_path = process_dir / f"{episode_stem}.article-brief.md"
    context_path = process_dir / f"{episode_stem}.article-context.json"
    writing_skill_snapshot_path = process_dir / f"{episode_stem}.writing-skill.md"

    resolved_highlights_path = _resolve_highlights_path(
        srt_path,
        out_dir,
        episode_stem,
        workspace_dir=process_dir,
        highlights_path=highlights_path,
        discover_highlights=discover_highlights,
    )
    highlights = (
        resolved_highlights_path.read_text(encoding="utf-8")
        if resolved_highlights_path
        else ""
    )
    # 访谈附件只参与 auto 判型或显式访谈。显式 monologue 必须只读当前 SRT，
    # 不能被同目录残留的 speaker transcript / guest profile 偷换素材。
    speaker_labeled = ""
    guest_profile = ""
    if article_type in ("auto", "interview"):
        speaker_labeled = _read_speaker_labeled(
            srt_path, out_dir, episode_stem, workspace_dir=process_dir
        )
        guest_profile = _read_guest_profile(
            srt_path, out_dir, episode_stem, workspace_dir=process_dir
        )
    editorial_notes = _read_editorial_notes(
        srt_path, out_dir, episode_stem, workspace_dir=process_dir
    )
    resolved_type, type_source = resolve_article_type(
        article_type, highlights, speaker_labeled, guest_profile
    )
    resolved_surface = resolve_surface(surface, resolved_type)
    writing_skill = resolve_writing_skill(
        resolved_type,
        explicit_path=writing_skill_path,
    )
    writing_skill_context = writing_skill.prompt_context
    surface_guidance = _SURFACE_GUIDANCE[resolved_surface]
    # 访谈如果有说话人标注稿，直接把它作为主逐字稿，避免重复塞入两份长 transcript。
    text = speaker_labeled if speaker_labeled else srt_to_timed_text(srt_path)
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars] + "…（已截断）"
    transcript_label = "本期说话人标注逐字稿" if speaker_labeled else "本期逐字稿"

    source_context = (
        (
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
        + (
            "\n\n以下是嘉宾公开资料/截图信息。只在它服务本期主线时自然使用，且只能采用这里或逐字稿能支撑的事实，不要夸大：\n\n---\n"
            + guest_profile
            + "\n---"
            if guest_profile
            else ""
        )
        + (
            "\n\n以下是采访者或编辑在回看后的观察。把它们当作待验证的编辑假设，不是自动成立的事实：回到逐字稿寻找跨段证据、相关反证与人物反应，再决定是否采用以及采用多强的措辞。\n\n---\n"
            + editorial_notes
            + "\n---"
            if editorial_notes
            else ""
        )
        + f"\n\n以下是{transcript_label}：\n\n---\n"
        + text
        + "\n---"
    )

    brief_prompt = (
        STYLE_BRIEF
        + "\n\n## 本次文章契约\n\n"
        + f"- 类型：{resolved_type}\n- Surface：{resolved_surface}\n- 形态说明：{surface_guidance}\n"
        + "\n## 当前唯一主责 writing skill\n\n"
        + writing_skill_context
        + "\n\n"
        + ARTICLE_SYSTEM_BRIEF
        + "\n\n## 本期判型后的查证重点\n\n"
        + _TYPE_EDITORIAL_FOCUS[resolved_type]
        + "\n\n"
        + ARTICLE_BRIEF_INSTRUCTION
        + source_context
    )
    run_id = uuid.uuid4().hex
    attempt_prefix = f".{episode_stem}.{run_id}"
    attempt_brief_path = process_dir / f"{attempt_prefix}.article-brief.md"
    attempt_context_path = process_dir / f"{attempt_prefix}.article-context.json"
    attempt_snapshot_path = process_dir / f"{attempt_prefix}.writing-skill.md"
    attempt_output_path = out_dir / f"{attempt_prefix}.article.md"
    attempt_paths = (
        attempt_brief_path,
        attempt_context_path,
        attempt_snapshot_path,
        attempt_output_path,
    )

    try:
        attempt_snapshot_path.write_text(writing_skill.content, encoding="utf-8")
        call_content_file_based(
            brief_prompt,
            attempt_brief_path,
            model=DEFAULT_CONTENT_MODEL,
        )
        article_brief = attempt_brief_path.read_text(encoding="utf-8")

        prompt = (
            STYLE_BRIEF
            + "\n\n## 本次文章契约\n\n"
            + f"- 类型：{resolved_type}\n- Surface：{resolved_surface}\n- 形态说明：{surface_guidance}\n"
            + "\n## 当前唯一主责 writing skill\n\n"
            + writing_skill_context
            + "\n\n"
            + ARTICLE_SYSTEM_BRIEF
            + "\n\n## 本期判型后的查证重点\n\n"
            + _TYPE_EDITORIAL_FOCUS[resolved_type]
            + "\n\n"
            + ARTICLE_INSTRUCTION
            + "\n\n以下 article brief 是第一轮编辑提出的 provisional hypotheses，不是新的事实源。逐字稿、speaker labels 与本期有来源的辅助资料优先；正文动笔前逐项复核 brief 的引语、时间戳、跨段判断和反证，冲突时丢弃 brief 中的说法。采用经复核的核心 substance、关键证据与本 surface 需要的观看导航，不要把 brief 写成清单：\n\n---\n"
            + article_brief
            + "\n---"
            + source_context
        )

        call_content_file_based(
            prompt,
            attempt_output_path,
            model=DEFAULT_CONTENT_MODEL,
        )
        attempt_context_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "article_type": resolved_type,
                    "article_type_source": type_source,
                    "surface": resolved_surface,
                    "highlights_path": (
                        str(resolved_highlights_path)
                        if resolved_highlights_path
                        else None
                    ),
                    "writing_skill": {
                        "name": writing_skill.name,
                        "source": writing_skill.source,
                        "path": str(writing_skill.path),
                        "sha256": writing_skill.sha256,
                        "snapshot_path": str(writing_skill_snapshot_path),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # 两轮模型都成功后再成组晋升；任一文件提交失败时
        # 恢复上一次的 article/brief/context/skill snapshot 完整组合。
        commit_prepared_files(
            [
                (attempt_snapshot_path, writing_skill_snapshot_path),
                (attempt_brief_path, brief_path),
                (attempt_output_path, output_path),
                (attempt_context_path, context_path),
            ]
        )
    finally:
        for attempt_path in attempt_paths:
            attempt_path.unlink(missing_ok=True)
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
    parser.add_argument(
        "--workspace-dir",
        default=None,
        help="article brief/context/editorial notes 工作区（默认 <视频名>_process）",
    )
    parser.add_argument(
        "--highlights",
        default=None,
        help="显式指定本期 highlights.md；提供后不再自动搜索",
    )
    parser.add_argument(
        "--writing-skill",
        default=None,
        help="显式指定 writing skill 或此前保存的主文件；完整复现还需相同代码与素材",
    )
    parser.add_argument(
        "--article-type",
        choices=("auto", "interview", "monologue"),
        default="auto",
        help="文章素材类型；auto 只在有 speaker/profile/highlights 信号时判型",
    )
    parser.add_argument(
        "--surface",
        choices=("auto", "article", "community", "companion", "release"),
        default="auto",
        help="发布形态；auto=访谈伴读、单口独立文章",
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
            workspace_dir=(
                Path(args.workspace_dir).resolve() if args.workspace_dir else None
            ),
            article_type=args.article_type,
            surface=args.surface,
            highlights_path=(
                Path(args.highlights).resolve() if args.highlights else None
            ),
            writing_skill_path=(
                Path(args.writing_skill).resolve() if args.writing_skill else None
            ),
        )
        print(f"  ✓ 文章已写入：{out.name}")
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
