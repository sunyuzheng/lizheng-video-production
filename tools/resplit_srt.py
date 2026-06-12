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
  python3 tools/resplit_srt.py input.corrected.srt              # → input.final.srt
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

_TERM_REPLACEMENTS = [
    ("NortonNeo", "Norton Neo"),
    ("ClaudeCode", "Claude Code"),
    ("CloudCode", "Claude Code"),
    ("ClaudeCodex", "Claude、Codex"),
    ("OpusCursor", "Opus、Cursor"),
    ("ChatGPTGemini", "ChatGPT、Gemini"),
    ("NotebookLMAgent", "NotebookLM、Agent"),
    ("AgentAgentsMCPContext", "Agent、Agents、MCP、Context"),
    ("documentfirst", "Document First"),
    ("Documentfirst", "Document First"),
    ("contextintake", "context intake"),
    ("Contextintake", "context intake"),
    ("DynamicWorkflow", "Dynamic Workflow"),
    ("WayWorkFlow", "Workflow"),
    ("coinstructor", "co-instructor"),
    ("sayeachother", "say hi to each other"),
    ("AgenticRed", "Agentic Red"),
    ("Dollarsh", "DoorDash"),
    ("TheageofAI", "The Age of AI"),
    ("hasbegun", "has begun"),
    ("usergeneratedsoftware", "user-generated software"),
    ("usergenerated", "user-generated"),
    ("professionalgenerateds", "professional-generated s"),
    ("professionalgenerative", "professional-generated"),
    ("generatedentertainment", "generated entertainment"),
    ("ShareProjects", "Share Projects"),
    ("behelpful", "be helpful"),
    ("contextcuration", "context curation"),
    ("ContextCuration", "Context Curation"),
    ("CursorCodex", "Cursor、Codex"),
    ("knowledgebank", "knowledge bank"),
    ("knifeedgeofexperience", "knife edge of experience"),
    ("contextarchitecture", "context architecture"),
    ("agenticloop", "agentic loop"),
    ("crossdomainleverage", "cross-domain leverage"),
    ("AInativedesign", "AI native design"),
    ("DocumentFirst", "Document First"),
    ("contextcontextcomponent", "Context、Component"),
    ("contextcriteria", "Context、Criteria"),
    ("solutionCER", "solution。CER"),
    ("contexterror", "Context、Error"),
    ("intheinthegame", "in the game"),
    ("reviewreview", "review、review"),
    ("roundroundround", "round、round、round"),
    ("DeepSeekDeepSeek", "DeepSeek"),
    ("intellectualhonesty", "intellectual honesty"),
    ("Skillrepo", "Skill repo"),
    ("GoogleDoc", "Google Doc"),
    ("AgentAgents", "Agent、Agents"),
    ("agentsmd", "AGENTS.md"),
    ("intakedashboard", "intake dashboard"),
    ("AppleWatch", "Apple Watch"),
    ("superlinearacademysuperlineardotacademy", "Superlinear Academy，superlinear.academy"),
    ("WebCoding", "Web Coding"),
]

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
    if len(text) <= max_chars:
        return [text]

    segments: list[str] = []

    # 第一刀：在句末标点后切（保留标点）
    sentence_parts = re.split(r"(?<=[。！？])", text)
    sentence_parts = [p.strip() for p in sentence_parts if p.strip()]

    for part in sentence_parts:
        if len(part) <= max_chars:
            segments.append(part)
            continue

        # 第二刀：在子句标点后切
        clause_parts = re.split(r"(?<=[，；、：])", part)
        clause_parts = [p.strip() for p in clause_parts if p.strip()]

        buf = ""
        for cp in clause_parts:
            if len(buf) + len(cp) <= max_chars:
                buf += cp
            else:
                if buf:
                    segments.append(buf)
                # cp 本身还是太长 → 按词边界切，不切开词
                if len(cp) > max_chars:
                    packed = _pack_tokens(_tokenize(cp), max_chars)
                    segments.extend(packed[:-1])
                    buf = packed[-1] if packed else ""
                else:
                    buf = cp
        if buf:
            segments.append(buf)

    return segments if segments else [text]


def _tokenize(text: str) -> list[str]:
    """切成不可再分的单元：jieba 词（保留空格 token）；无 jieba 时退回空格切分。"""
    if _HAS_JIEBA:
        return list(jieba.cut(text))
    parts = text.split(" ")
    return [p + " " for p in parts[:-1]] + parts[-1:]


def _pack_tokens(tokens: list[str], max_chars: int) -> list[str]:
    """把 token 依序装进 ≤max_chars 的行里，单个超长 token 才强制截断。"""
    out: list[str] = []
    buf = ""
    for tok in tokens:
        if len(buf) + len(tok) <= max_chars:
            buf += tok
        else:
            if buf.strip():
                out.append(buf.strip())
            while len(tok) > max_chars:
                out.append(tok[:max_chars])
                tok = tok[max_chars:]
            buf = tok
    if buf.strip():
        out.append(buf.strip())
    return out


def normalize_text(text: str) -> str:
    """Clean common no-space artifacts from mlx-qwen3-asr subtitle output."""
    text = text.strip()
    for src, dst in _TERM_REPLACEMENTS:
        text = text.replace(src, dst)
    text = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9][A-Za-z0-9+_.-]*)", r"\1 \2", text)
    text = re.sub(r"([A-Za-z0-9][A-Za-z0-9+_.-]*)([\u4e00-\u9fff])", r"\1 \2", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.replace("AI 技校", "AI技校")
    return text


# ── SRT 解析（轻量版，不依赖 correct_srt）────────────────────────────────────

def _parse_srt(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n{2,}", content.strip())
    chunks = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        ts_line = next((l for l in lines if "-->" in l), "")
        text_lines = [l for l in lines
                      if not l.strip().isdigit() and "-->" not in l]
        text = normalize_text(" ".join(text_lines))
        if not text or not ts_line:
            continue
        chunks.append({"timestamp": ts_line, "text": text})
    return chunks


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

    def close():
        nonlocal cur
        if cur and cur["text"]:
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
            if gap > MERGE_MAX_GAP or len(cur["text"]) + n > MERGE_MAX_CHARS:
                close()

        if cur is None:
            cur = {"text": "", "char_starts": [], "end": t_end}

        if _needs_space(cur["text"], text):
            cur["text"] += " "
            cur["char_starts"].append(t_start)
        cur["text"] += text
        cur["char_starts"].extend(starts)
        cur["end"] = t_end

    close()
    return windows


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


# ── 主函数 ────────────────────────────────────────────────────────────────────

def resplit_srt(
    input_path: Path,
    output_path: Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Path:
    """
    读取 input_path (.corrected.srt 或 .qwen.srt)，
    先把停顿很小的相邻 cue 合并成窗口（跨越 ASR 的坏边界），
    再断成 ≤ max_chars 字符的条目，时间戳按字符比例插值，
    写入 output_path（默认为 input_path 同目录的 .final.srt）。
    """
    if output_path is None:
        stem = input_path.name
        for suf in (".corrected.srt", ".qwen.srt", ".srt"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        output_path = input_path.parent / f"{stem}.final.srt"

    chunks = _parse_srt(input_path)
    result: list[dict] = []

    for window in _merge_windows(chunks):
        segments = split_text(window["text"], max_chars)
        if not segments:
            continue
        for seg, (t0, t1) in zip(segments, _segment_times(window, segments)):
            result.append({"timestamp": _fmt_range(t0, t1), "text": seg})

    with open(output_path, "w", encoding="utf-8") as f:
        for i, c in enumerate(result, 1):
            f.write(f"{i}\n{c['timestamp']}\n{c['text']}\n\n")

    return output_path


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="SRT 断句工具")
    parser.add_argument("input", help="输入 SRT 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出路径（默认 .final.srt）")
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
    print(f"✓ {len(list(open(out).read().split('\n\n')))-1} 条 → {out.name}")


if __name__ == "__main__":
    main()
