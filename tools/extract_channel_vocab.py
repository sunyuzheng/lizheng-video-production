#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_channel_vocab.py — 从 kedaibiao-channel 的历史数据提取频道词汇表

策略：
  1. 从 error_notebook.jsonl 的 human 侧提取多字符正确形式（品牌名、人名、术语）
  2. 从人工精校 SRT 提取高频英文专有名词（大写开头且 ≥3 个视频出现）
  3. 合并成 channel_vocab.json，供转录时 context= 注入和校对时规则替换用

运行时输出：data/channel_vocab.json（只含实际消费且已验证的字段）
可选审计输出：通过 --audit-output 保存原始统计，不进入公共 runtime JSON

用法：
  python3 tools/extract_channel_vocab.py
  python3 tools/extract_channel_vocab.py --min-videos 2 --min-errors 3
  python3 tools/extract_channel_vocab.py --hotwords-file verified_hotwords.txt \
    --audit-output episode_process/channel_vocab.audit.json
"""

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

# ── 配置 ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_DEFAULT_CHANNEL = _ROOT.parent / "kedaibiao-channel"
_DEFAULT_CANDIDATES = _ROOT / "data" / "verified_corrections.json"
_DEFAULT_HOTWORDS = _ROOT / "data" / "verified_hotwords.txt"
_DEFAULT_OUTPUT = _ROOT / "data" / "channel_vocab.json"

# 词汇表阈值
MIN_VIDEO_COUNT = 3    # 英文专有名词：至少在几个视频的人工SRT里出现
MIN_ERROR_COUNT = 5    # 错误映射：至少出现几次
MIN_TERM_LEN = 2       # 最短词长（字符数）

# 常见英文词过滤列表（不是频道专有词）
_COMMON_EN = frozenset("""
the a an in on at to of is it as be or and for but not so by
we me he she they you your our my his her its their
do did does doing done have has had get got go going
ai ok dr mr ms yeah right yes no oh well so now
just like when what where how why who which this that
one two three also very much more most about after
work works working make makes let us can will be
""".split())

# ─────────────────────────────────────────────────────────────────────────────

_TS_RE = re.compile(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}")
# 英文专有名词：首字母大写，或全大写缩写，长度≥2
_EN_PROPER = re.compile(r"\b([A-Z][A-Za-z0-9\-\.]{1,}|[A-Z]{2,})\b")


def parse_srt_text(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    lines = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.isdigit() or _TS_RE.search(line):
            continue
        lines.append(line)
    return " ".join(lines)


def resolve_channel_root(cli_value: str | None = None) -> Path:
    """Resolve channel data as CLI flag > environment > conventional sibling."""
    value = cli_value or os.environ.get("KEDAIBIAO_CHANNEL_ROOT")
    return Path(value).expanduser().resolve() if value else _DEFAULT_CHANNEL.resolve()


def validate_artifact_paths(
    read_paths: dict[str, Path | None],
    write_paths: dict[str, Path | None],
) -> None:
    """Prevent generated runtime/audit files from overwriting reviewed sources."""
    reads = {
        path.resolve(): role
        for role, path in read_paths.items()
        if path is not None
    }
    writes: dict[Path, str] = {}
    for role, path in write_paths.items():
        if path is None:
            continue
        resolved = path.resolve()
        if resolved in reads:
            raise ValueError(
                f"output path for {role} would overwrite source {reads[resolved]}: {resolved}"
            )
        if resolved in writes:
            raise ValueError(
                f"output paths for {writes[resolved]} and {role} must differ: {resolved}"
            )
        writes[resolved] = role


def find_srt_pairs(channel_root: Path) -> list[tuple[Path, Path]]:
    pairs = []
    human_suffixes = (".zh.srt", ".en-zh.srt", ".zh-Hans.srt", ".zh-Hant.srt")
    archive_dirs = [
        channel_root / "archive" / "有人工字幕",
        channel_root / "archive" / "会员视频",
    ]
    for base in archive_dirs:
        if not base.exists():
            continue
        for qwen in sorted(base.rglob("*.qwen.srt")):
            folder = qwen.parent
            stem = qwen.name.replace(".qwen.srt", "")
            for suf in human_suffixes:
                candidate = folder / (stem + suf)
                if candidate.exists():
                    pairs.append((qwen, candidate))
                    break
    return pairs


def extract_english_proper_nouns(pairs: list[tuple[Path, Path]], min_videos: int) -> dict:
    """
    从人工精校 SRT 提取大写开头的英文专有名词（≥min_videos 个视频出现）。
    这些词是频道常用的品牌/术语，Qwen 容易拼错。
    """
    term_videos: dict[str, set] = defaultdict(set)
    for qwen_path, human_path in pairs:
        text = parse_srt_text(human_path)
        for m in _EN_PROPER.finditer(text):
            word = m.group()
            if word.lower() in _COMMON_EN:
                continue
            if len(word) < MIN_TERM_LEN:
                continue
            term_videos[word].add(str(human_path))
    return {term: len(vids) for term, vids in term_videos.items()
            if len(vids) >= min_videos}


def extract_from_error_notebook(
    min_count: int, error_notebook: Path
) -> tuple[dict, dict, dict]:
    """
    从 error_notebook.jsonl 提取：
    - multi_char_map: 多字符词的 qwen→correct（含数字格式、多字词）
    - name_brand_map: 人名/品牌词
    - bidirectional_skip: 双向混淆的单字词（需要上下文，不能规则化）

    返回 (multi_char_map, name_brand_map, single_char_unidirectional)
    """
    if not error_notebook.exists():
        print(f"  警告：找不到 {error_notebook}")
        return {}, {}, {}

    pair_count: Counter = Counter()
    pair_meta: dict = {}

    with open(error_notebook, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                q, h = e["qwen"], e["human"]
                pair_count[(q, h)] += 1
                pair_meta[(q, h)] = e.get("category", "other")
            except Exception:
                continue

    multi_char: dict = {}
    name_brand: dict = {}
    single_unidirectional: dict = {}

    for (q, h), cnt in pair_count.items():
        if cnt < min_count:
            continue
        # 跳过明显无意义的（单个字母替换）
        if len(q) <= 1 and len(h) <= 1:
            reverse = pair_count.get((h, q), 0)
            if reverse >= min_count:
                continue  # 双向单字符混淆，需要上下文
            # 单向单字符：也可以收集
            single_unidirectional[q] = {"correct": h, "count": cnt,
                                         "category": pair_meta.get((q, h), "other")}
            continue

        cat = pair_meta.get((q, h), "other")
        entry = {"correct": h, "count": cnt, "category": cat}

        # 人名/品牌判断：category 包含 name/brand，或 correct 含大写英文字母
        if cat in ("name", "brand") or re.search(r"[A-Z]{2}", h):
            name_brand[q] = entry
        else:
            # 多字符词（含数字格式如百分之十→10%）
            if len(q) >= 2 or len(h) >= 2:
                # 双向检查：如果双向都高频，需要上下文
                reverse_cnt = pair_count.get((h, q), 0)
                if reverse_cnt >= min_count and len(q) <= 2 and len(h) <= 2:
                    continue  # 双向短词混淆
                multi_char[q] = entry

    return multi_char, name_brand, single_unidirectional


def load_existing_candidates(candidates_path: Path) -> dict:
    """Load the tracked, manually verified correction candidates."""
    if not candidates_path.exists():
        raise FileNotFoundError(f"verified corrections file not found: {candidates_path}")
    try:
        payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"verified corrections JSON is invalid: {candidates_path}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise ValueError("verified corrections must be a JSON object")
    for pattern, info in payload.items():
        if not isinstance(pattern, str) or not pattern.strip() or not isinstance(info, dict):
            raise ValueError(f"invalid verified correction entry: {pattern!r}")
        alternatives = info.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives or not all(
            isinstance(value, str) and value.strip() for value in alternatives
        ):
            raise ValueError(
                f"verified correction {pattern!r} needs non-empty string alternatives"
            )
        if "hint" in info and not isinstance(info["hint"], str):
            raise ValueError(f"verified correction {pattern!r} hint must be a string")
    return payload


def load_verified_hotwords(path: Path | None) -> list[str]:
    """Load one explicitly reviewed ASR hotword per line; comments start with #."""
    if path is None:
        return []
    if not path.exists():
        raise FileNotFoundError(f"verified hotwords file not found: {path}")
    result: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        term = raw_line.strip()
        if not term or term.startswith("#") or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


def build_hotwords_context(verified_hotwords: list[str]) -> str:
    """Build ASR context from an explicit reviewed list, never inferred maps."""
    if not verified_hotwords:
        return ""
    return "以下是本频道已确认的相关词汇，供参考：\n" + "、".join(verified_hotwords)


def build_runtime_vocab(
    verified_candidates: dict, verified_hotwords: list[str]
) -> dict:
    """Build the minimal schema consumed by ASR context and subtitle correction."""
    runtime_candidates = {
        pattern: {
            "alternatives": list(info["alternatives"]),
            "hint": info.get("hint", ""),
        }
        for pattern, info in verified_candidates.items()
    }
    return {
        "schema_version": 2,
        "verified_candidates": runtime_candidates,
        "hotwords_context": build_hotwords_context(verified_hotwords),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-videos", type=int, default=MIN_VIDEO_COUNT)
    parser.add_argument("--min-errors", type=int, default=MIN_ERROR_COUNT)
    parser.add_argument(
        "--channel-root",
        default=None,
        help=(
            "kedaibiao-channel 数据仓库；默认读环境变量 "
            "KEDAIBIAO_CHANNEL_ROOT，再回退到当前仓库的同级目录"
        ),
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=_DEFAULT_CANDIDATES,
        help="仓内已追踪、人工确认的 verified_corrections.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="输出 channel_vocab.json",
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=None,
        help="可选：把 inferred nouns / raw mappings 写入单独的本地审计 JSON",
    )
    parser.add_argument(
        "--hotwords-file",
        type=Path,
        default=(
            Path(os.environ["LIZHENG_VERIFIED_HOTWORDS_FILE"])
            if os.environ.get("LIZHENG_VERIFIED_HOTWORDS_FILE")
            else _DEFAULT_HOTWORDS
        ),
        help=(
            "逐行列出人工确认的 ASR hotwords；默认使用仓内 "
            "data/verified_hotwords.txt，可由环境变量覆盖"
        ),
    )
    args = parser.parse_args()

    channel_root = resolve_channel_root(args.channel_root)
    error_notebook = channel_root / "logs" / "error_notebook.jsonl"
    print(f"频道数据：{channel_root}")

    print("扫描 SRT 配对…")
    pairs = find_srt_pairs(channel_root)
    print(f"  找到 {len(pairs)} 对 Qwen+人工 SRT")

    source_paths: dict[str, Path | None] = {
        "verified_corrections": args.candidates,
        "verified_hotwords": args.hotwords_file,
        "error_notebook": error_notebook,
    }
    for index, (qwen_path, human_path) in enumerate(pairs):
        source_paths[f"qwen_srt_{index}"] = qwen_path
        source_paths[f"human_srt_{index}"] = human_path
    validate_artifact_paths(
        source_paths,
        {"runtime_vocab": args.output, "audit_output": args.audit_output},
    )

    print(f"提取英文专有名词（首字母大写，≥{args.min_videos} 个视频）…")
    en_proper = extract_english_proper_nouns(pairs, args.min_videos)
    print(f"  {len(en_proper)} 个英文专有词")

    print(f"从 error_notebook 提取纠错映射（≥{args.min_errors} 次）…")
    multi_char, name_brand, single_uni = extract_from_error_notebook(
        args.min_errors, error_notebook
    )
    print(f"  多字符纠错：{len(multi_char)} 条")
    print(f"  人名/品牌纠错：{len(name_brand)} 条")
    print(f"  单字符单向纠错：{len(single_uni)} 条（供参考，不直接规则化）")

    print("加载已验证的 corrections…")
    existing = load_existing_candidates(args.candidates.resolve())
    print(f"  {len(existing)} 条已验证规则")

    verified_hotwords = load_verified_hotwords(
        args.hotwords_file.resolve() if args.hotwords_file else None
    )
    print(f"  {len(verified_hotwords)} 个已确认 ASR hotwords")

    analysis = {
        "meta": {
            "source_pairs": len(pairs),
            "min_video_count": args.min_videos,
            "min_error_count": args.min_errors,
        },
        "english_proper_nouns": en_proper,
        "name_brand_corrections": name_brand,
        "multi_char_corrections": multi_char,
        "single_char_unidirectional": single_uni,
    }
    runtime_vocab = build_runtime_vocab(existing, verified_hotwords)

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(runtime_vocab, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✓ 写入 {output}")

    if args.audit_output:
        audit_output = args.audit_output.resolve()
        audit_output.parent.mkdir(parents=True, exist_ok=True)
        audit_output.write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"✓ 审计统计写入 {audit_output}")

    print("\n=== 英文专有名词（Top 20）===")
    for term, cnt in sorted(en_proper.items(), key=lambda x: -x[1])[:20]:
        print(f"  {cnt:3d}x  {term}")

    print("\n=== 人名/品牌纠错（Top 15）===")
    for q, info in sorted(name_brand.items(), key=lambda x: -x[1]["count"])[:15]:
        print(f"  {info['count']:3d}x  {q!r:20s} → {info['correct']!r}")

    print("\n=== 多字符纠错（Top 20）===")
    for q, info in sorted(multi_char.items(), key=lambda x: -x[1]["count"])[:20]:
        print(f"  {info['count']:3d}x  {q!r:20s} → {info['correct']!r}  ({info['category']})")

    print("\n=== Hotwords context ===")
    print(runtime_vocab["hotwords_context"])


if __name__ == "__main__":
    main()
