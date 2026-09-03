#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resplit_srt.py — 把 SRT 条目断成 ≤N 字的显示友好格式

先合并再重切：ASR 的原始 cue 边界经常落在词中间（如「…这是你的事 / 情当然…」），
所以先把停顿很小的相邻 cue 合并成窗口（按字符插值保留每个 cue 的时间锚点），
再对窗口整体断句。

断句优先级：
  1. 句末标点（。！？）—— 最优先，保证语义完整
  2. 子句标点（，；、：）—— 次优先
  3. 词边界（jieba 分词；无 jieba 时退回空格边界）
  4. 强制截断（极端情况兜底）

时间戳按字符数比例插值（中文每字等权，英文字符按实际长度）。

用法：
  python3 tools/resplit_srt.py input.corrected.srt              # → input.final.candidate.srt
  python3 tools/resplit_srt.py input.corrected.srt --max-chars 25
  python3 tools/resplit_srt.py input.corrected.srt -o out.srt
"""

import re
import sys
from pathlib import Path

try:
    import jieba
    jieba.setLogLevel(60)  # 关闭初始化日志
    _HAS_JIEBA = True
except ImportError:
    _HAS_JIEBA = False

DEFAULT_MAX_CHARS = 20
MERGE_MAX_GAP = 0.6      # 秒：cue 间停顿超过此值视为真实停顿，不跨越合并
MERGE_MAX_CHARS = 200    # 合并窗口字符上限，限制时间戳插值误差累积
DEFAULT_MAX_CPS = 25.0
DEFAULT_MIN_DURATION = 0.2
NEAR_LIMIT_FALLBACK_MIN_RUN = 6

_SENTENCE_END_RE = re.compile(r"[。！？!?](?:[”’\"'）)\]】》〉」』]*)\s*$")
_CLAUSE_END_RE = re.compile(r"[；;，,：:、.…](?:[”’\"'）)\]】》〉」』]*)\s*$")
_SOURCE_BREAK_RE = re.compile(r"[。！？!?；;，,：:、.…]")

_LATIN_TOKEN_RE = re.compile(
    r"^[A-Za-z0-9]+(?:['’+_.-][A-Za-z0-9]+)*(?:%|\.com)?$"
)


def _visible_len(text: str) -> int:
    """Count readable CJK/Latin/digit content, excluding spaces/punctuation."""
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))

# ── 时间戳解析 / 格式化 ───────────────────────────────────────────────────────

_TS_RE = re.compile(
    r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)"
)


def _parse_ts(ts_line: str) -> tuple[float, float]:
    m = _TS_RE.search(ts_line)
    if not m:
        return 0.0, 0.0
    h1, m1, s1, ms1, h2, m2, s2, ms2 = [int(x) for x in m.groups()]
    start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
    end   = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
    return start, end


def _fmt_ts(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h,  ms = divmod(ms, 3_600_000)
    m,  ms = divmod(ms,    60_000)
    s,  ms = divmod(ms,     1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_range(t_start: float, t_end: float) -> str:
    return f"{_fmt_ts(t_start)} --> {_fmt_ts(t_end)}"


# ── 文本断句 ──────────────────────────────────────────────────────────────────

def split_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """
    将 text 切成每段 ≤ max_chars 字符的列表。
    尽量在标点处切，保持语义完整。
    """
    text = text.strip()
    if not text:
        return []
    if _visible_len(text) <= max_chars:
        return [text]

    segments: list[str] = []

    # 第一刀：在句末标点后切（保留标点）
    sentence_parts = re.split(r"(?<=[。！？])", text)
    sentence_parts = [p.strip() for p in sentence_parts if p.strip()]

    for part in sentence_parts:
        if _visible_len(part) <= max_chars:
            segments.append(part)
            continue

        # 第二刀：在子句标点后切
        clause_parts = re.split(r"(?<=[，；、：])", part)
        clause_parts = [p.strip() for p in clause_parts if p.strip()]

        buf = ""
        for cp in clause_parts:
            if _visible_len(buf + cp) <= max_chars:
                buf += cp
            else:
                if buf:
                    segments.append(buf)
                # cp 本身还是太长 → 按词边界切，不切开词
                if _visible_len(cp) > max_chars:
                    packed = _pack_tokens(_tokenize(cp), max_chars)
                    segments.extend(packed[:-1])
                    buf = packed[-1] if packed else ""
                else:
                    buf = cp
        if buf:
            segments.append(buf)

    return segments if segments else [text]


def _tokenize(text: str) -> list[str]:
    """Preserve complete Latin tokens; only use jieba inside CJK runs."""
    atoms = re.findall(
        r"[A-Za-z0-9]+(?:['’+_.-][A-Za-z0-9]+)*(?:%|\.com)?"
        r"|[\u4e00-\u9fff]+|\s+|[^\s]",
        text,
    )
    out: list[str] = []
    for atom in atoms:
        if _HAS_JIEBA and re.fullmatch(r"[\u4e00-\u9fff]+", atom):
            out.extend(jieba.cut(atom))
        else:
            out.append(atom)
    return out


def _pack_tokens(tokens: list[str], max_chars: int) -> list[str]:
    """Pack tokens without ever splitting a Latin word across subtitle cues."""
    out: list[str] = []
    buf = ""
    for tok in tokens:
        if _visible_len(buf + tok) <= max_chars:
            buf += tok
        else:
            if buf.strip():
                out.append(buf.strip())
            if _LATIN_TOKEN_RE.fullmatch(tok.strip()):
                # A rare >N-character product name is preferable to a broken
                # word; subtitle_qc.py will flag it for a manual display choice.
                buf = tok
                continue
            while _visible_len(tok) > max_chars:
                cut = 0
                visible = 0
                for cut, char in enumerate(tok, 1):
                    visible += int(bool(re.match(r"[\u4e00-\u9fffA-Za-z0-9]", char)))
                    if visible >= max_chars:
                        break
                out.append(tok[:cut].strip())
                tok = tok[cut:]
            buf = tok
    if buf.strip():
        out.append(buf.strip())
    return out


def normalize_text(text: str) -> str:
    """Normalize only whitespace at CJK/Latin boundaries; never correct semantics."""
    text = text.strip()
    text = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9][A-Za-z0-9+_.-]*)", r"\1 \2", text)
    text = re.sub(r"([A-Za-z0-9][A-Za-z0-9+_.-]*)([\u4e00-\u9fff])", r"\1 \2", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


# ── SRT 解析（轻量版，不依赖 correct_srt）────────────────────────────────────

def _parse_srt(path: Path) -> list[dict]:
    # Reuse the delivery parser so malformed source blocks cannot disappear
    # before the final QC step has a chance to inspect them.
    from subtitle_qc import parse_srt

    return [
        {
            "timestamp": f"{cue['start_stamp']} --> {cue['end_stamp']}",
            "text": normalize_text(" ".join(cue["text"].splitlines())),
        }
        for cue in parse_srt(path)
    ]


# ── 合并窗口：跨越 ASR 的坏 cue 边界 ─────────────────────────────────────────

def _needs_space(left: str, right: str) -> bool:
    """cue 拼接处是否补空格：拉丁-拉丁、拉丁-中文边界都补（与 normalize_text 惯例一致）。"""
    if not left or not right:
        return False
    l_latin = bool(re.match(r"[A-Za-z0-9]", left[-1]))
    r_latin = bool(re.match(r"[A-Za-z0-9]", right[0]))
    l_cjk = bool(re.match(r"[一-鿿]", left[-1]))
    r_cjk = bool(re.match(r"[一-鿿]", right[0]))
    return (l_latin and (r_latin or r_cjk)) or (l_cjk and r_latin)


def _merge_windows(chunks: list[dict]) -> list[dict]:
    """
    把停顿 ≤ MERGE_MAX_GAP 的相邻 cue 合并成窗口。
    每个窗口返回 {"text", "char_starts"（每个字符的起始时间）, "end"}，
    时间锚点保留自原始 cue：cue 内部字符按时长等权插值。
    """
    windows: list[dict] = []
    cur: dict | None = None

    def close(reason: str):
        nonlocal cur
        if cur and cur["text"]:
            cur["end_reason"] = reason
            windows.append(cur)
        cur = None

    for chunk in chunks:
        t_start, t_end = _parse_ts(chunk["timestamp"])
        text = chunk["text"]
        if not text:
            continue
        n = len(text)
        duration = max(t_end - t_start, 0.0)
        starts = [t_start + duration * k / n for k in range(n)]

        if cur is not None:
            gap = t_start - cur["end"]
            if gap > MERGE_MAX_GAP:
                close("pause_window")
            elif gap < -MERGE_MAX_GAP:
                close("timestamp_discontinuity")
            elif len(cur["text"]) + n > MERGE_MAX_CHARS:
                close("merge_size_cap")

        if cur is None:
            cur = {
                "text": "",
                "char_starts": [],
                "end": t_end,
                "end_reason": None,
            }

        if _needs_space(cur["text"], text):
            cur["text"] += " "
            cur["char_starts"].append(t_start)
        cur["text"] += text
        cur["char_starts"].extend(starts)
        cur["end"] = t_end

    close("source_end")
    return windows


def _boundary_source(segment: str, *, is_last: bool, window_reason: str) -> str:
    """Describe why a segment boundary exists without changing split behavior."""
    if _SENTENCE_END_RE.search(segment):
        return "sentence_punct"
    if _CLAUSE_END_RE.search(segment):
        return "clause_punct"
    if is_last:
        return window_reason
    return "token_fallback"


def _longest_unpunctuated_run(text: str) -> int:
    longest = 0
    current = 0
    for char in text:
        if _SOURCE_BREAK_RE.match(char):
            longest = max(longest, current)
            current = 0
        elif re.match(r"[\u4e00-\u9fffA-Za-z0-9]", char):
            current += 1
    return max(longest, current)


def _build_split_diagnostics(
    chunks: list[dict],
    result: list[dict],
    boundary_sources: list[str],
    *,
    max_chars: int,
) -> dict:
    """Summarize whether rule-based re-splitting degraded into token packing."""
    source_text = "".join(chunk["text"] for chunk in chunks)
    visible_chars = _visible_len(source_text)
    punctuation_breaks = len(_SOURCE_BREAK_RE.findall(source_text))
    breaks_per_100 = punctuation_breaks * 100 / max(visible_chars, 1)
    longest_unpunctuated_run = _longest_unpunctuated_run(source_text)

    # The final source_end is not a choice made by the splitter.  Every other
    # cue ending is an observable boundary and belongs in the provenance mix.
    observed = boundary_sources[:-1] if boundary_sources else []
    fallback_kinds = {"token_fallback", "merge_size_cap"}
    fallback_positions = [
        i for i, source in enumerate(observed) if source in fallback_kinds
    ]
    fallback_count = len(fallback_positions)
    fallback_ratio = fallback_count / max(len(observed), 1)
    near_limit_floor = max(1, max_chars - 2)
    near_limit_fallback_count = sum(
        _visible_len(result[i]["text"]) >= near_limit_floor
        for i in fallback_positions
    )
    near_limit_fallback_ratio = near_limit_fallback_count / max(fallback_count, 1)
    longest_near_limit_fallback_run = 0
    current_run = 0
    fallback_position_set = set(fallback_positions)
    for i in range(len(observed)):
        is_near_limit_fallback = (
            i in fallback_position_set
            and _visible_len(result[i]["text"]) >= near_limit_floor
        )
        if is_near_limit_fallback:
            current_run += 1
            longest_near_limit_fallback_run = max(
                longest_near_limit_fallback_run, current_run
            )
        else:
            current_run = 0

    source_counts: dict[str, int] = {}
    for source in observed:
        source_counts[source] = source_counts.get(source, 0) + 1

    enough_source = visible_chars >= max(400, 20 * max_chars)
    low_punctuation = (
        enough_source
        and breaks_per_100 < 0.5
        and longest_unpunctuated_run >= 8 * max_chars
    )
    fallback_saturation = (
        fallback_count >= 10
        and fallback_ratio >= 0.75
        and near_limit_fallback_ratio >= 0.60
    )
    local_fallback_run = (
        enough_source
        and longest_near_limit_fallback_run >= NEAR_LIMIT_FALLBACK_MIN_RUN
    )
    reason_codes: list[str] = []
    if low_punctuation:
        reason_codes.append("LOW_PUNCTUATION")
    if fallback_saturation:
        reason_codes.append("FALLBACK_BOUNDARY_SATURATION")
    if local_fallback_run:
        reason_codes.append("NEAR_LIMIT_FALLBACK_RUN")

    # Gate on a continuous bad region.  Whole-video averages remain useful
    # diagnostics, but they can both hide a local failure and overstate many
    # isolated fallbacks.
    requires_semantic_review = local_fallback_run
    return {
        "method": "split_provenance",
        "risk": requires_semantic_review,
        "requires_semantic_review": requires_semantic_review,
        "reason_codes": reason_codes,
        "visible_chars": visible_chars,
        "punctuation_breaks": punctuation_breaks,
        "breaks_per_100": breaks_per_100,
        "longest_unpunctuated_run": longest_unpunctuated_run,
        "boundary_source_counts": source_counts,
        "fallback_boundary_count": fallback_count,
        "fallback_boundary_ratio": fallback_ratio,
        "near_limit_floor": near_limit_floor,
        "near_limit_fallback_count": near_limit_fallback_count,
        "near_limit_fallback_ratio": near_limit_fallback_ratio,
        "longest_near_limit_fallback_run": longest_near_limit_fallback_run,
    }


def _segment_times(
    window: dict, segments: list[str]
) -> list[tuple[float, float]]:
    """把断句结果映射回窗口的字符时间轴（顺序匹配，跳过被 strip 掉的空白）。"""
    text, starts, w_end = window["text"], window["char_starts"], window["end"]
    times: list[tuple[float, float]] = []
    i = 0
    for seg in segments:
        seg_start: float | None = None
        for c in seg:
            while i < len(text) and text[i] != c:
                i += 1
            if i < len(text):
                if seg_start is None:
                    seg_start = starts[i]
                i += 1
        seg_end = starts[i] if i < len(starts) else w_end
        times.append((seg_start if seg_start is not None else seg_end, seg_end))
    return times


def _repair_display_timing(
    result: list[dict],
    *,
    max_cps: float = DEFAULT_MAX_CPS,
    min_duration: float = DEFAULT_MIN_DURATION,
) -> list[dict]:
    """Fix tiny/fast cues by borrowing only nearby slack or silence.

    This keeps the outer boundary of a local speech cluster fixed.  It avoids
    the common failure where a valid English word becomes a 0.1–0.3s cue after
    character-proportional re-timing.
    """
    cues = []
    for item in result:
        start, end = _parse_ts(item["timestamp"])
        cues.append({"start": start, "end": end, "text": item["text"]})

    for i, cue in enumerate(cues):
        target = max(min_duration, _visible_len(cue["text"]) / max_cps)
        deficit = target - (cue["end"] - cue["start"])
        if deficit <= 0.0005:
            continue

        prev_end = cues[i - 1]["end"] if i else 0.0
        take = min(deficit, max(0.0, cue["start"] - prev_end))
        cue["start"] -= take
        deficit -= take

        next_start = cues[i + 1]["start"] if i + 1 < len(cues) else cue["end"]
        take = min(deficit, max(0.0, next_start - cue["end"]))
        cue["end"] += take
        deficit -= take
        if deficit <= 0.0005:
            continue

        donors: list[tuple[float, int]] = []
        for donor_idx in range(max(0, i - 3), min(len(cues), i + 4)):
            if donor_idx == i:
                continue
            lo, hi = sorted((donor_idx, i))
            if any(cues[k + 1]["start"] - cues[k]["end"] > 0.05 for k in range(lo, hi)):
                continue
            donor = cues[donor_idx]
            floor = max(min_duration, _visible_len(donor["text"]) / max_cps)
            slack = donor["end"] - donor["start"] - floor
            if slack > 0.001:
                donors.append((slack, donor_idx))
        if not donors:
            continue

        slack, donor_idx = max(donors)
        take = min(deficit, slack)
        if donor_idx < i:
            cues[donor_idx]["end"] -= take
            for k in range(donor_idx + 1, i):
                cues[k]["start"] -= take
                cues[k]["end"] -= take
            cue["start"] -= take
        else:
            cue["end"] += take
            for k in range(i + 1, donor_idx):
                cues[k]["start"] += take
                cues[k]["end"] += take
            cues[donor_idx]["start"] += take

    return [
        {"timestamp": _fmt_range(cue["start"], cue["end"]), "text": cue["text"]}
        for cue in cues
    ]


# ── 主函数 ────────────────────────────────────────────────────────────────────

def resplit_srt(
    input_path: Path,
    output_path: Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    diagnostics: dict | None = None,
) -> Path:
    """
    读取 input_path (.corrected.srt 或 .qwen.srt)，
    先把停顿很小的相邻 cue 合并成窗口（跨越 ASR 的坏边界），
    再断成 ≤ max_chars 字符的条目，时间戳按字符比例插值，
    写入 output_path（默认为 input_path 同目录的
    .final.candidate.srt，通过字幕 QC 后才可晋升为 .final.srt）。
    """
    if output_path is None:
        stem = input_path.name
        for suf in (".corrected.srt", ".qwen.srt", ".srt"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        output_path = input_path.parent / f"{stem}.final.candidate.srt"
    if input_path.resolve() == output_path.resolve():
        raise ValueError("断句输出不能覆盖输入 SRT")

    chunks = _parse_srt(input_path)
    result: list[dict] = []
    boundary_sources: list[str] = []

    for window in _merge_windows(chunks):
        segments = split_text(window["text"], max_chars)
        if not segments:
            continue
        for segment_index, (seg, (t0, t1)) in enumerate(
            zip(segments, _segment_times(window, segments))
        ):
            result.append({"timestamp": _fmt_range(t0, t1), "text": seg})
            boundary_sources.append(
                _boundary_source(
                    seg,
                    is_last=segment_index == len(segments) - 1,
                    window_reason=window["end_reason"],
                )
            )

    result = _repair_display_timing(result)

    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(
            _build_split_diagnostics(
                chunks,
                result,
                boundary_sources,
                max_chars=max_chars,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for i, c in enumerate(result, 1):
            f.write(f"{i}\n{c['timestamp']}\n{c['text']}\n\n")

    return output_path


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="SRT 断句工具")
    parser.add_argument("input", help="输入 SRT 文件路径")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="输出路径（默认 .final.candidate.srt，需通过 QC 后晋升）",
    )
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                        help=f"每条最大字符数（默认 {DEFAULT_MAX_CHARS}）")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"错误：文件不存在 {input_path}")
        sys.exit(1)

    out = resplit_srt(
        input_path,
        output_path=Path(args.output).resolve() if args.output else None,
        max_chars=args.max_chars,
    )
    block_count = len(
        [block for block in out.read_text(encoding="utf-8").split("\n\n") if block.strip()]
    )
    print(f"✓ {block_count} 条 → {out.name}")


if __name__ == "__main__":
    main()
