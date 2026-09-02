#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
process_video.py v4 — 视频转录 + 字幕校对 + 高光 + 文章 + 标题 + YouTube description 一体化入口

七步流程：
  1. Qwen3-ASR 转录
  2. Codex 字幕校对
  3. 断句处理
  4. 提取视频高光候选（数量由材料决定，供编辑与标题流程使用）
  5. 生成频道风格文章
  6. 生成播客标题（观众认知转变 brief + challenger，Codex-first）
  7. 生成 YouTube description（介绍 + 章节）

用法：
  python3 tools/process_video.py video.mp4
  python3 tools/process_video.py video.mp4 --skip-transcribe
  python3 tools/process_video.py video.mp4 --seeds 刘嘉 "Superlinear Academy"
  python3 tools/process_video.py video.mp4 --no-seeds
  python3 tools/process_video.py video.mp4 --skip-article
  python3 tools/process_video.py video.mp4 --skip-highlights   # 跳过高光提取
  python3 tools/process_video.py video.mp4 --skip-titles
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TOOLS = Path(__file__).parent
_ROOT  = _TOOLS.parent
sys.path.insert(0, str(_TOOLS / "correct"))

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".mp4", ".mov", ".flac", ".ogg", ".webm"}
QWEN_MODEL = "Qwen/Qwen3-ASR-1.7B"
QWEN_LANG  = "Chinese"
CODEX_CORRECTION_MODEL = (
    os.environ.get("LIZHENG_CODEX_CORRECTION_MODEL")
    or os.environ.get("LIZHENG_CODEX_MODEL")
    or None
)
CODEX_CONTENT_MODEL = (
    os.environ.get("LIZHENG_CODEX_CONTENT_MODEL")
    or os.environ.get("LIZHENG_CODEX_MODEL")
    or None
)
CLAUDE_FALLBACK_MODEL = (
    os.environ.get("LIZHENG_CLAUDE_FALLBACK_MODEL")
    or os.environ.get("LIZHENG_CLAUDE_MODEL")
    or None
)
_VOCAB_FILE = _ROOT / "data" / "channel_vocab.json"


def load_channel_context() -> str:
    """从 channel_vocab.json 读取预构建的 hotwords context 字符串"""
    if not _VOCAB_FILE.exists():
        return ""
    try:
        vocab = json.loads(_VOCAB_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"  ⚠ 无法读取频道 hotwords，当前转写不注入词表：{error}", file=sys.stderr)
        return ""
    if vocab.get("schema_version") != 2:
        print("  ⚠ channel_vocab schema 不是 v2，当前转写不注入词表", file=sys.stderr)
        return ""
    context = vocab.get("hotwords_context", "")
    if not isinstance(context, str):
        print("  ⚠ channel_vocab.hotwords_context 不是字符串，当前转写不注入词表", file=sys.stderr)
        return ""
    return context


def build_transcribe_context(channel_ctx: str, episode_seeds: list[str]) -> str:
    """把频道 context + 本期 seeds 合并成传给 Qwen3-ASR context= 的字符串"""
    parts = []
    if channel_ctx:
        parts.append(channel_ctx)
    if episode_seeds:
        parts.append("本期嘉宾/术语：" + "、".join(episode_seeds))
    return "\n".join(parts)


def ask_episode_seeds() -> list[str]:
    """交互式询问本期嘉宾名和特有术语"""
    print()
    print("┌─────────────────────────────────────────────────────────┐")
    print("│  转录前：请输入本期嘉宾名、公司名、特有术语（可选）      │")
    print("│  这些词会注入 ASR 引导解码，提高专有名词准确率          │")
    print("│  直接回车跳过                                            │")
    print("└─────────────────────────────────────────────────────────┘")
    seeds = []
    while True:
        try:
            val = input("  输入术语（回车结束）: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not val:
            break
        seeds.append(val)
        print(f"  ✓ 已添加：{val}")
    return seeds


def episode_stem(video_path: Path) -> str:
    return video_path.with_suffix("").name


def artifact_marker(status: str) -> str:
    """Render delivery status without making a successful reuse look like failure."""
    if status == "本次生成":
        return "✓"
    if status.startswith("显式复用"):
        return "↻"
    if status.startswith("已跳过"):
        return "→"
    return "✗"


def process_dir_for(video_path: Path) -> Path:
    return video_path.parent / f"{episode_stem(video_path)}_process"


def stage_legacy_qwen(legacy_qwen: Path, workspace_qwen: Path) -> Path:
    """Copy a legacy raw transcript into the workspace without touching source."""
    workspace_qwen.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{workspace_qwen.name}.staging.",
        dir=workspace_qwen.parent,
        delete=False,
    ) as handle:
        candidate = Path(handle.name)
    try:
        shutil.copy2(legacy_qwen, candidate)
        candidate.replace(workspace_qwen)
    finally:
        candidate.unlink(missing_ok=True)
    return workspace_qwen


def _resolve_asr_cli() -> str | None:
    """优先使用当前 Python 环境的 CLI，再回退到 PATH 和 Homebrew。"""
    venv_cli = Path(sys.executable).parent / "mlx-qwen3-asr"
    homebrew_cli = Path("/opt/homebrew/bin/mlx-qwen3-asr")
    for candidate in (str(venv_cli), "mlx-qwen3-asr", str(homebrew_cli)):
        cli = shutil.which(candidate)
        if cli:
            return cli
    return None


def transcribe(video_path: Path, output_dir: Path, context: str = "") -> Path:
    """Run mlx-qwen3-asr for this invocation and output <stem>.qwen.srt."""
    output_dir.mkdir(parents=True, exist_ok=True)
    qwen_srt = output_dir / f"{episode_stem(video_path)}.qwen.srt"

    venv_cli = Path(sys.executable).parent / "mlx-qwen3-asr"
    homebrew_cli = Path("/opt/homebrew/bin/mlx-qwen3-asr")
    cli = _resolve_asr_cli()
    if not cli:
        print(f"错误: 未找到 mlx-qwen3-asr CLI，请确认 {venv_cli} 或 {homebrew_cli} 可用")
        sys.exit(1)

    if context:
        print(f"  Context 注入（前100字）: {context[:100]}…", flush=True)

    print(f"  使用本地 mlx-qwen3-asr CLI: {cli}", flush=True)
    print(f"  模型: {QWEN_MODEL}", flush=True)
    t0 = time.time()
    print(f"  转录中: {video_path.name}", flush=True)
    with tempfile.TemporaryDirectory(prefix="lizheng-asr-run-") as temp_dir:
        run_dir = Path(temp_dir)
        cmd = [
            cli,
            str(video_path),
            "--model", QWEN_MODEL,
            "--language", QWEN_LANG,
            "--output-dir", str(run_dir),
            "--output-format", "srt",
            "--verbose",
        ]
        if context:
            cmd.extend(["--context", context])
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"  ✗ mlx-qwen3-asr 转录失败，退出码 {e.returncode}")
            sys.exit(e.returncode)

        expected = run_dir / f"{episode_stem(video_path)}.srt"
        generated_files = sorted(run_dir.rglob("*.srt"))
        if expected.exists():
            current_generated = expected
        elif len(generated_files) == 1:
            current_generated = generated_files[0]
        else:
            detail = f"找到 {len(generated_files)} 个候选" if generated_files else "没有候选"
            print(f"  ✗ mlx-qwen3-asr 本次未生成唯一可识别的 SRT（{detail}）")
            sys.exit(1)

        from subtitle_qc import parse_srt

        try:
            cues = parse_srt(current_generated)
        except ValueError as error:
            print(f"  ✗ mlx-qwen3-asr 产出的 SRT 结构无效：{error}")
            sys.exit(1)

        with tempfile.NamedTemporaryFile(
            prefix=f".{qwen_srt.name}.candidate.",
            dir=output_dir,
            delete=False,
        ) as handle:
            qwen_candidate = Path(handle.name)
        try:
            shutil.copy2(current_generated, qwen_candidate)
            qwen_candidate.replace(qwen_srt)
        finally:
            qwen_candidate.unlink(missing_ok=True)

        if not qwen_srt.exists():
            print("  ✗ mlx-qwen3-asr 本次未生成新的 SRT 文件")
            sys.exit(1)

    elapsed = time.time() - t0
    n = len(cues)
    print(f"  ✓ 转录完成  {n} 条  {elapsed:.0f}s  → {qwen_srt.name}")
    return qwen_srt


def correct(
    qwen_srt: Path,
    episode_seeds: list[str],
    model: str | None,
    timeout: int = 900,
) -> Path | None:
    from correct_srt import CodexUnavailableError, correct_file
    t0 = time.time()
    print(f"  Codex CLI 校对中…", flush=True)
    stats: dict = {}
    try:
        result = correct_file(
            qwen_srt,
            episode_seeds=episode_seeds,
            model=model,
            timeout=timeout,
            verbose=False,
            stats=stats,
        )
    except CodexUnavailableError as e:
        print(f"\n{'='*55}")
        print(f"✗ 字幕精校无法运行，流程中止\n")
        print(f"{e}")
        print(f"{'='*55}")
        sys.exit(1)
    elapsed = time.time() - t0
    if result:
        print(f"  ✓ 校对完成  {elapsed:.0f}s")
        warn_if_uncorrected(qwen_srt, result, stats)
    else:
        print(f"  ✗ 校对失败")
    return result


def warn_if_uncorrected(qwen_srt: Path, corrected_srt: Path, stats: dict) -> None:
    """
    质量门：LLM 修正数为 0 时告警。

    判据用 corrections 而不是「两文件内容相同」：格式规范化是纯规则层、
    与 codex 是否真的跑过无关，只要它改了一处，两文件就不再相同——
    正是这样一次改动会让基于内容比对的质量门漏掉「codex 根本没执行」。
    内容是否完全一致只作为附加的更强信号一并报出。
    """
    if stats.get("corrections", 0) > 0:
        return

    from correct_srt import parse_srt
    identical = None
    try:
        identical = ([c["text"] for c in parse_srt(qwen_srt)]
                     == [c["text"] for c in parse_srt(corrected_srt)])
    except Exception:
        pass

    print(f"  ⚠ 质量门：corrections=0，本步没有产生任何 LLM 精校修正")
    if identical:
        print(f"     且 {corrected_srt.name} 与 {qwen_srt.name} 文本完全一致")
    elif identical is False:
        print(f"     （文本有差异，但差异全部来自规则层格式规范化 fmt={stats.get('fmt', 0)}）")
    print(f"     可能本期确实无需修正，也可能校对引擎未真正生效。先确认引擎：")
    print(f"       which codex && codex --version && codex login status")
    print(f"     再决定是否重跑本步；确认无误后本条可忽略，但需人工精校复核。")


def resplit(corrected_srt: Path, output_path: Path, max_chars: int = 20) -> Path | None:
    sys.path.insert(0, str(_TOOLS))
    from resplit_srt import resplit_srt
    t0 = time.time()
    print(f"  断句处理（≤{max_chars}字/条）…", flush=True)
    try:
        result = resplit_srt(corrected_srt, output_path=output_path, max_chars=max_chars)
        elapsed = time.time() - t0
        n = sum(1 for line in result.read_text(encoding="utf-8").split("\n\n") if line.strip())
        print(f"  ✓ 断句完成  {n} 条  {elapsed:.0f}s")
        return result
    except Exception as e:
        print(f"  ✗ 断句失败: {e}")
        raise


def validate_and_export_subtitles(
    candidate_srt: Path,
    final_srt: Path,
    vtt_path: Path,
    report_path: Path,
    max_chars: int = 20,
) -> bool:
    """Validate a candidate and promote a matching SRT/VTT pair on success."""
    sys.path.insert(0, str(_TOOLS))
    from subtitle_qc import (
        inspect,
        parse_srt,
        promote_subtitle_pair,
        write_parse_error_report,
        write_report,
    )

    try:
        cues = parse_srt(candidate_srt)
    except ValueError as error:
        write_parse_error_report(report_path, error)
        print(f"  ✗ 字幕 QC 未通过：{error}")
        return False
    findings = inspect(cues, max_chars=max_chars, min_duration=0.2, max_cps=25.0)
    write_report(cues, findings, report_path)
    failed = sum(len(items) for items in findings.values())
    if failed:
        print(
            "  ✗ 字幕 QC 未通过："
            f"invalid={len(findings['invalid'])} "
            f"overlaps={len(findings['overlaps'])} "
            f"long={len(findings['long'])} "
            f"short={len(findings['short'])} fast={len(findings['fast'])}"
        )
        return False

    promote_subtitle_pair(candidate_srt, cues, final_srt, vtt_path)
    print(f"  ✓ 字幕 QC 通过并导出 VTT → {vtt_path.name}")
    return True


def article(
    final_srt: Path,
    output_dir: Path,
    workspace_dir: Path,
    stem: str,
    article_type: str,
    surface: str,
    highlights_path: Path | None = None,
    discover_highlights: bool = True,
    writing_skill_path: Path | None = None,
) -> Path | None:
    sys.path.insert(0, str(_TOOLS))
    from generate_article import generate_article
    t0 = time.time()
    print(f"  生成文章…", flush=True)
    try:
        result = generate_article(
            final_srt,
            output_dir=output_dir,
            workspace_dir=workspace_dir,
            stem=stem,
            article_type=article_type,
            surface=surface,
            highlights_path=highlights_path,
            discover_highlights=discover_highlights,
            writing_skill_path=writing_skill_path,
        )
        elapsed = time.time() - t0
        print(f"  ✓ 文章完成  {elapsed:.0f}s  → {result.name}")
        return result
    except Exception as e:
        print(f"  ✗ 文章生成失败: {e}")
        return None


def highlights(srt_path: Path, output_dir: Path, stem: str) -> Path | None:
    sys.path.insert(0, str(_TOOLS))
    from generate_highlights import generate_highlights
    t0 = time.time()
    print(f"  提取高光片段…", flush=True)
    try:
        result = generate_highlights(srt_path, output_dir=output_dir, stem=stem)
        elapsed = time.time() - t0
        print(f"  ✓ 高光完成  {elapsed:.0f}s  → {result.name}")
        return result
    except Exception as e:
        print(f"  ✗ 高光提取失败: {e}")
        return None


def titles(
    content_path: Path,
    output_dir: Path,
    workspace_dir: Path,
    stem: str,
    highlights_path: Path | None = None,
    discover_highlights: bool = True,
) -> Path | None:
    sys.path.insert(0, str(_TOOLS))
    from generate_titles import generate_titles
    t0 = time.time()
    print(f"  生成标题（观众认知转变 brief + challenger）…", flush=True)
    try:
        result = generate_titles(
            content_path,
            output_dir=output_dir,
            workspace_dir=workspace_dir,
            stem=stem,
            highlights_path=highlights_path,
            discover_highlights=discover_highlights,
        )
        elapsed = time.time() - t0
        print(f"  ✓ 标题完成  {elapsed:.0f}s  → {result.name}")
        return result
    except Exception as e:
        print(f"  ✗ 标题生成失败: {e}")
        return None


def youtube_description(final_srt: Path, output_dir: Path, stem: str) -> Path | None:
    sys.path.insert(0, str(_TOOLS))
    from generate_youtube_description import generate_youtube_description
    t0 = time.time()
    print(f"  生成 YouTube description…", flush=True)
    try:
        result = generate_youtube_description(final_srt, output_dir=output_dir, stem=stem)
        elapsed = time.time() - t0
        print(f"  ✓ YouTube description 完成  {elapsed:.0f}s  → {result.name}")
        return result
    except Exception as e:
        print(f"  ✗ YouTube description 生成失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="视频转录 + 字幕校对 + 高光 + 文章 + 标题 + YouTube description v4")
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument("--skip-transcribe", action="store_true")
    parser.add_argument("--skip-correct", action="store_true")
    parser.add_argument(
        "--subtitle-source",
        default=None,
        help="显式指定已有 SRT 作为字幕源；跳过 ASR 与校对，不自动猜测旧 corrected/final",
    )
    parser.add_argument("--skip-article", action="store_true",
                        help="跳过文章生成")
    parser.add_argument("--skip-highlights", action="store_true",
                        help="跳过高光提取")
    parser.add_argument("--skip-titles", action="store_true",
                        help="跳过标题生成")
    parser.add_argument("--skip-youtube-description", action="store_true",
                        help="跳过 YouTube description 生成")
    parser.add_argument(
        "--article-type",
        choices=("auto", "interview", "monologue"),
        default="auto",
        help="文章素材类型；auto 从 speaker/profile/highlights 判型",
    )
    parser.add_argument(
        "--article-surface",
        choices=("auto", "article", "community", "companion", "release"),
        default="auto",
        help="文章发布形态；auto=访谈伴读、单口独立文章",
    )
    parser.add_argument(
        "--article-writing-skill",
        default=None,
        help="显式指定 writing skill 或此前保存的主文件；完整复现还需相同代码与素材",
    )
    parser.add_argument("--seeds", nargs="*", default=None,
                        help="本期嘉宾/术语（跳过交互式询问）")
    parser.add_argument("--no-seeds", action="store_true",
                        help="跳过 seeds 输入（不询问也不注入）")
    parser.add_argument(
        "--model",
        default=CODEX_CORRECTION_MODEL,
        help="Codex CLI 字幕校对模型（默认使用 Codex 配置）",
    )
    parser.add_argument(
        "--process-dir",
        default=None,
        help="过程文件目录（默认 <视频名>_process，最终交付仍在视频同目录）",
    )
    parser.add_argument(
        "--correction-timeout",
        type=int,
        default=900,
        help="Codex 全文字幕校对超时秒数（默认 900）",
    )
    parser.add_argument("--max-chars", type=int, default=20,
                        help="断句：每条字幕最大字符数（默认 20）")
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"错误: 文件不存在: {video_path}")
        sys.exit(1)
    stem = episode_stem(video_path)
    delivery_dir = video_path.parent
    process_dir = Path(args.process_dir).resolve() if args.process_dir else process_dir_for(video_path)
    process_dir.mkdir(parents=True, exist_ok=True)
    explicit_subtitle_source = (
        Path(args.subtitle_source).expanduser().resolve()
        if args.subtitle_source
        else None
    )
    if explicit_subtitle_source and not explicit_subtitle_source.is_file():
        parser.error(f"--subtitle-source 不存在: {explicit_subtitle_source}")

    print(f"\n{'='*55}")
    print(f"视频: {video_path.name}")
    print(f"交付目录: {delivery_dir}")
    print(f"过程目录: {process_dir}")
    correction_model = args.model or "Codex CLI 默认配置"
    print(f"校对引擎: Codex CLI ({correction_model})")
    print(
        "高光/文章/标题/description: "
        f"Codex ({CODEX_CONTENT_MODEL or 'CLI 默认配置'}) → "
        f"Claude fallback ({CLAUDE_FALLBACK_MODEL or 'CLI 默认配置'})"
    )
    print(f"流程: 转录 → 校对 → 断句 → 高光 → 文章 → 标题 → YouTube description")
    print(f"{'='*55}")

    # ── 决定 episode_seeds ───────────────────────────────────────────────────
    if args.no_seeds:
        episode_seeds = []
    elif args.seeds is not None:
        episode_seeds = [s.strip() for s in args.seeds if s.strip()]
        if episode_seeds:
            print(f"\n种子术语：{episode_seeds}")
    else:
        episode_seeds = ask_episode_seeds()
        if episode_seeds:
            print(f"  种子术语已确认：{episode_seeds}")

    # ── 1. 转录 ───────────────────────────────────────────────────────────────
    if explicit_subtitle_source:
        qwen_srt = explicit_subtitle_source
        print(f"\n[1/7] ASR 已跳过（显式字幕源） → {qwen_srt}")
    elif not args.skip_transcribe:
        print("\n[1/7] Qwen3-ASR 转录")
        channel_ctx = load_channel_context()
        context = build_transcribe_context(channel_ctx, episode_seeds)
        qwen_srt = transcribe(video_path, output_dir=process_dir, context=context)
    else:
        qwen_srt = process_dir / f"{stem}.qwen.srt"
        legacy_qwen = delivery_dir / f"{stem}.qwen.srt"
        if not qwen_srt.exists() and legacy_qwen.exists():
            qwen_srt = stage_legacy_qwen(legacy_qwen, qwen_srt)
            print(f"  已将 legacy raw 字幕复制到工作区：{qwen_srt}")
        if not qwen_srt.exists():
            print(
                f"错误: --skip-transcribe 但找不到 {qwen_srt.name}。"
                "若要使用 corrected/final，请显式传 --subtitle-source PATH。"
            )
            sys.exit(1)
        else:
            print(f"\n[1/7] 转录 (已跳过) → {qwen_srt.name}")

    # 记录「本该跑却失败」的步骤。跳过不算失败，最后据此决定退出码——
    # 全流程恒 exit 0 会让失败在自动化里完全看不见。
    failures: list[str] = []
    qc_path = process_dir / f"{stem}.subtitle_qc.md"

    # ── 2. 校对 ───────────────────────────────────────────────────────────────
    corrected_srt = None
    if explicit_subtitle_source:
        corrected_srt = None
        print(f"\n[2/7] 校对已跳过（显式字幕源） → {qwen_srt}")
    elif not args.skip_correct:
        print("\n[2/7] Codex 字幕校对 + 全文扫描")
        try:
            corrected_srt = correct(
                qwen_srt,
                episode_seeds,
                model=args.model,
                timeout=args.correction_timeout,
            )
        except ValueError as error:
            from subtitle_qc import write_parse_error_report

            write_parse_error_report(qc_path, error)
            print(f"  ✗ 字幕结构无法校对：{error}")
            print(f"  诊断报告：{qc_path}")
            sys.exit(1)
        if not corrected_srt:
            failures.append("[2/7] 字幕校对")
    else:
        corrected_srt = None
        print(f"\n[2/7] 校对 (已跳过) → 后续使用 {qwen_srt}")

    # ── 3. 断句 ───────────────────────────────────────────────────────────────
    if not args.skip_correct and not explicit_subtitle_source and not corrected_srt:
        print("  ✗ 本次要求字幕精校，但没有生成 corrected.srt；不使用旧稿或原始 ASR 降级。")
        sys.exit(1)

    print(f"\n[3/7] 断句处理 + 字幕 QC")
    final_srt = None
    final_path = delivery_dir / f"{stem}.final.srt"
    candidate_path = process_dir / f"{stem}.final.candidate.srt"
    subtitle_source = corrected_srt if corrected_srt and corrected_srt.exists() else qwen_srt
    explicit_final_reuse = bool(
        explicit_subtitle_source
        and explicit_subtitle_source.resolve() == final_path.resolve()
    )
    if explicit_final_reuse:
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(subtitle_source, candidate_path)
        candidate_srt = candidate_path
        print("  显式复用现有 final.srt：不重切文字，重跑 QC 并生成同文 VTT。")
    else:
        try:
            candidate_srt = resplit(
                subtitle_source, output_path=candidate_path, max_chars=args.max_chars
            )
        except Exception as error:
            from subtitle_qc import write_parse_error_report

            write_parse_error_report(qc_path, error)
            print(f"  诊断报告：{qc_path}")
            print("  ✗ 断句失败；后续内容资产不会生成。")
            sys.exit(1)

    vtt_path = delivery_dir / f"{stem}.final.vtt"
    if not validate_and_export_subtitles(
        candidate_srt,
        final_path,
        vtt_path,
        qc_path,
        max_chars=args.max_chars,
    ):
        print(f"  诊断稿保留在过程目录：{candidate_srt}")
        print("  既有 final.srt/final.vtt 未被覆盖；后续内容资产不会生成。")
        sys.exit(1)
    final_srt = final_path
    artifact_status: dict[str, str] = {
        ".final.srt": (
            "显式复用（本次 QC 通过）"
            if explicit_final_reuse
            else "本次生成"
        ),
        ".final.vtt": "本次生成",
    }

    # ── 4. 提取高光 ───────────────────────────────────────────────────────────
    highlights_path = None
    if not args.skip_highlights:
        print(f"\n[4/7] 提取视频高光片段")
        src = final_srt
        if src and src.exists():
            highlights_path = highlights(src, output_dir=delivery_dir, stem=stem)
            if not highlights_path:
                failures.append("[4/7] 高光提取")
                artifact_status[".highlights.md"] = (
                    "本次失败（旧文件已保留）"
                    if (delivery_dir / f"{stem}.highlights.md").exists()
                    else "本次失败"
                )
            else:
                artifact_status[".highlights.md"] = "本次生成"
        else:
            print("  (无可用 SRT，跳过)")
            failures.append("[4/7] 高光提取（无可用 SRT）")
            artifact_status[".highlights.md"] = "本次失败"
    else:
        candidate = delivery_dir / f"{stem}.highlights.md"
        artifact_status[".highlights.md"] = (
            "已跳过（旧文件存在，未复用）"
            if candidate.exists()
            else "已跳过"
        )
        print(f"\n[4/7] 高光提取 (已跳过)")

    # 主流程只消费本次明确生成的高光。skip 或生成失败时，
    # downstream 回到 final SRT，不自动发现同目录旧文件。
    discover_highlights = False

    # ── 5. 生成文章 ───────────────────────────────────────────────────────────
    article_path = None
    if not args.skip_article:
        print(f"\n[5/7] 生成频道风格文章")
        src = final_srt
        if src and src.exists():
            article_path = article(
                src,
                output_dir=delivery_dir,
                workspace_dir=process_dir,
                stem=stem,
                article_type=args.article_type,
                surface=args.article_surface,
                highlights_path=highlights_path,
                discover_highlights=discover_highlights,
                writing_skill_path=(
                    Path(args.article_writing_skill).resolve()
                    if args.article_writing_skill
                    else None
                ),
            )
            if not article_path:
                failures.append("[5/7] 文章生成")
                artifact_status[".article.md"] = (
                    "本次失败（旧文件已保留）"
                    if (delivery_dir / f"{stem}.article.md").exists()
                    else "本次失败"
                )
            else:
                artifact_status[".article.md"] = "本次生成"
        else:
            print("  (无可用 SRT，跳过)")
            failures.append("[5/7] 文章生成（无可用 SRT）")
            artifact_status[".article.md"] = "本次失败"
    else:
        candidate = delivery_dir / f"{stem}.article.md"
        artifact_status[".article.md"] = (
            "已跳过（旧文件存在，未复用）"
            if candidate.exists()
            else "已跳过"
        )
        print(f"\n[5/7] 文章生成 (已跳过)")

    # ── 6. 生成标题 ───────────────────────────────────────────────────────────
    if not args.skip_titles:
        print(f"\n[6/7] 生成播客标题（观众认知转变驱动）")
        # 优先用 article，其次 final_srt — highlights 会通过文件名自动检测
        src = article_path or final_srt
        if src and src.exists():
            titles_path = titles(
                src,
                output_dir=delivery_dir,
                workspace_dir=process_dir,
                stem=stem,
                highlights_path=highlights_path,
                discover_highlights=discover_highlights,
            )
            if not titles_path:
                failures.append("[6/7] 标题生成")
                artifact_status[".titles.md"] = (
                    "本次失败（旧文件已保留）"
                    if (delivery_dir / f"{stem}.titles.md").exists()
                    else "本次失败"
                )
            else:
                artifact_status[".titles.md"] = "本次生成"
        else:
            print("  (无可用来源，跳过)")
            failures.append("[6/7] 标题生成（无可用来源）")
            artifact_status[".titles.md"] = "本次失败"
    else:
        artifact_status[".titles.md"] = (
            "已跳过（旧文件存在）"
            if (delivery_dir / f"{stem}.titles.md").exists()
            else "已跳过"
        )
        print(f"\n[6/7] 标题生成 (已跳过)")

    # ── 7. 生成 YouTube description ──────────────────────────────────────────
    if not args.skip_youtube_description:
        print(f"\n[7/7] 生成 YouTube description")
        src = final_srt
        if src and src.exists():
            description_path = youtube_description(
                src, output_dir=delivery_dir, stem=stem
            )
            if not description_path:
                failures.append("[7/7] YouTube description")
                artifact_status[".youtube-description.txt"] = (
                    "本次失败（旧文件已保留）"
                    if (delivery_dir / f"{stem}.youtube-description.txt").exists()
                    else "本次失败"
                )
            else:
                artifact_status[".youtube-description.txt"] = "本次生成"
        else:
            print("  (无可用 SRT，跳过)")
            failures.append("[7/7] YouTube description（无可用 SRT）")
            artifact_status[".youtube-description.txt"] = "本次失败"
    else:
        artifact_status[".youtube-description.txt"] = (
            "已跳过（旧文件存在）"
            if (delivery_dir / f"{stem}.youtube-description.txt").exists()
            else "已跳过"
        )
        print(f"\n[7/7] YouTube description 生成 (已跳过)")

    print(f"\n{'='*55}")
    print("交付文件：")
    for suf in [
        ".final.srt",
        ".final.vtt",
        ".highlights.md",
        ".article.md",
        ".titles.md",
        ".youtube-description.txt",
    ]:
        p = delivery_dir / f"{stem}{suf}"
        status = artifact_status.get(suf, "本次未生成")
        marker = artifact_marker(status)
        print(f"  {marker} {p.name} — {status}")
    print("过程文件（仅显示当前存在性，不代表本次生成）：")
    for suf in [".qwen.srt", ".corrected.srt", ".final.candidate.srt", ".subtitle_qc.md"]:
        p = process_dir / f"{stem}{suf}"
        print(f"  {'✓' if p.exists() else '✗'} {p.name}")
    title_ws = process_dir / f"{stem}_title_ws"
    print(f"  {'✓' if title_ws.exists() else '✗'} {title_ws.name}/")
    print()

    if failures:
        print(f"{'='*55}")
        print(f"✗ {len(failures)} 个步骤未成功完成：")
        for f in failures:
            print(f"    {f}")
        print("  标 ✓ 的文件是本次生成；同名旧文件不能抵消本次的 ✗/→ 状态。")
        print(f"{'='*55}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
