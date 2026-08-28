#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
correct_srt.py v4 — 候选词驱动校对引擎（Codex CLI 文件响应模式）

相比 v3 的改动：
  - 字幕精校改用 Codex CLI 文件响应模式（codex_cli.call_codex_file_based）

相比 v2 的改动：
  - 去掉多 provider 直接 API 调用（Anthropic/OpenAI/Gemini SDK）
  - 改用文件响应模式
  - 候选词扫描 + 全文扫描合并为单次 CLI 调用，全文上下文更完整
  - 无需 API Key 配置，使用已登录的 CLI

用法：
  from tools.correct.correct_srt import correct_file
  correct_file(qwen_path, episode_seeds=["刘嘉", "Superlinear Academy"])
"""

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

# 确保 repo root 在 sys.path 中（process_video.py 会把 tools/correct/ 插入路径，导致 tools 包找不到）
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.codex_cli import DEFAULT_CODEX_MODEL, call_codex_file_based

# ── 配置 ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent.parent
_VOCAB_FILE = _ROOT / "data" / "channel_vocab.json"

MAX_EDIT_RATIO = 0.20   # 批量修改预算；短字幕保留 15 字符底额
# ─────────────────────────────────────────────────────────────────────────────

# ── 格式规范化规则（规则直接执行，不走 LLM）──────────────────────────────────
# format: {pattern: replacement, ...}，带 boundary_guard 的需要额外检查
_FORMAT_RULES: list[dict] = [
    {"pat": "百分之百", "rep": "100%"},
    {"pat": "百分之十", "rep": "10%"},
    {"pat": "两百",     "rep": "200",  "boundary_guard": True},   # 一两百 不改
    {"pat": "两千",     "rep": "2000", "boundary_guard": True},
]
_BOUNDARY_PRECEDING = set("一二三四五六七八九十")
_FORMAT_PAT_SET = {r["pat"] for r in _FORMAT_RULES}
# ─────────────────────────────────────────────────────────────────────────────


# ── 前置检查：codex 必须可用 ──────────────────────────────────────────────────

class CodexUnavailableError(RuntimeError):
    """codex CLI 不在 PATH 上。校对必须硬失败，不能静默产出与输入相同的字幕。"""


def ensure_codex_available() -> str:
    """
    入口前置检查。校对这一步不做引擎降级：换模型会无声改变校对质量和风格，
    跳过则会把未精校字幕当作已精校，后续断句/高光/文章/标题全部受影响。
    """
    codex = shutil.which("codex")
    if not codex:
        raise CodexUnavailableError(
            "未找到 codex CLI —— 字幕精校无法运行。\n"
            "  修复：brew reinstall --cask codex\n"
            "  确认：which codex && codex --version && codex login status\n"
            "  确实要跳过校对时，显式使用 process_video.py --skip-correct。"
        )
    return codex


def load_vocab() -> dict:
    if not _VOCAB_FILE.exists():
        return {}
    vocab = json.loads(_VOCAB_FILE.read_text(encoding="utf-8"))
    if not isinstance(vocab, dict) or vocab.get("schema_version") != 2:
        raise ValueError("channel_vocab.json 必须使用 schema_version=2")
    if not isinstance(vocab.get("verified_candidates", {}), dict):
        raise ValueError("channel_vocab.json 的 verified_candidates 必须是对象")
    return vocab


def build_candidates(vocab: dict) -> dict:
    """
    从频道词表构建需结合语境判断的 candidates dict。
    本期嘉宾／术语不作为替换候选，而是单独注入 prompt 作为参考写法。
    格式与 v7 相同：{pattern: {"alternatives": [...], "hint": "..."}}
    注意：格式规范化规则不在这里（已提前到规则层），candidates 只处理需要 LLM 判断的
    """
    candidates: dict = {}

    # 1. 来自 channel_vocab 的已验证候选词（去掉纯格式规范化的）
    for pat, info in vocab.get("verified_candidates", {}).items():
        if pat in _FORMAT_PAT_SET:
            continue  # 格式规则已在规则层处理
        alts = info.get("alternatives", [])
        candidates[pat] = {"alternatives": alts, "hint": info.get("hint", "")}

    return candidates


def parse_srt(path: Path) -> list[dict]:
    from tools.subtitle_qc import parse_srt as parse_srt_strict

    return [
        {
            "timestamp": f"{cue['start_stamp']} --> {cue['end_stamp']}",
            "text": cue["text"],
        }
        for cue in parse_srt_strict(path)
    ]


def write_srt(chunks: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(chunks, 1):
            f.write(f"{i}\n{c['timestamp']}\n{c['text']}\n\n")


# ── 规则层：格式规范化（不走 LLM）──────────────────────────────────────────

def apply_format_rules(chunks: list[dict]) -> tuple[list[dict], int]:
    """直接替换数字格式（百分之十→10% 等），返回修改后的 chunks 和改动数"""
    result = [dict(c) for c in chunks]
    count = 0
    for rule in _FORMAT_RULES:
        pat, rep = rule["pat"], rule["rep"]
        boundary = rule.get("boundary_guard", False)
        for chunk in result:
            text = chunk["text"]
            if pat not in text:
                continue
            if boundary:
                # 不替换「一两百」「三两千」这类前面跟着数量字的情况
                new_text = ""
                i = 0
                while i < len(text):
                    pos = text.find(pat, i)
                    if pos == -1:
                        new_text += text[i:]
                        break
                    if pos > 0 and text[pos - 1] in _BOUNDARY_PRECEDING:
                        new_text += text[i:pos + len(pat)]  # 保留原文
                    else:
                        new_text += text[i:pos] + rep
                        count += 1
                    i = pos + len(pat)
                chunk["text"] = new_text
            else:
                replaced = text.replace(pat, rep)
                if replaced != text:
                    count += text.count(pat)
                chunk["text"] = replaced
    return result, count


# ── 候选词扫描 ────────────────────────────────────────────────────────────────

def scan_flags(chunks: list[dict], candidates: dict) -> list[dict]:
    flags = []
    sorted_pats = sorted(candidates.keys(), key=len, reverse=True)
    already: set = set()
    single_seen: set = set()

    for ci, chunk in enumerate(chunks):
        text = chunk["text"]
        for pat in sorted_pats:
            info = candidates[pat]
            is_single = len(pat) == 1
            start = 0
            while True:
                pos = text.find(pat, start)
                if pos == -1:
                    break
                key = (ci, pos)
                if is_single:
                    sk = (ci, pat)
                    if sk not in single_seen:
                        single_seen.add(sk)
                        flags.append({
                            "chunk_idx": ci, "found": pat,
                            "alternatives": info.get("alternatives", []),
                            "hint": info.get("hint", ""),
                            "context": text[max(0, pos-10): pos+len(pat)+10],
                            "is_single": True,
                        })
                else:
                    if key not in already:
                        already.add(key)
                        flags.append({
                            "chunk_idx": ci, "found": pat,
                            "alternatives": info.get("alternatives", []),
                            "hint": info.get("hint", ""),
                            "context": text[max(0, pos-10): pos+len(pat)+10],
                            "is_single": False,
                        })
                start = pos + 1
    return flags


# ── Codex CLI 校对调用 ───────────────────────────────────────────────────────

def build_correction_prompt(
    chunks: list[dict],
    flags: list[dict],
    episode_seeds: list[str] | None = None,
) -> str:
    """构建完整校对 prompt（合并候选词扫描 + 全文扫描）"""
    srt_lines = []
    for ci, chunk in enumerate(chunks):
        srt_lines.append(f"[{ci}] {chunk.get('timestamp', '')}")
        srt_lines.append(chunk["text"])
        srt_lines.append("")
    srt_text = "\n".join(srt_lines)

    if flags:
        single_hints: dict = {}
        multi_hints: list = []
        for f in flags:
            if f.get("is_single"):
                h = f["hint"] or f"「{f['found']}」可能是「{'或'.join(f['alternatives'])}」"
                single_hints[f["hint"] or f["found"]] = h
            else:
                alts = "、".join(f["alternatives"]) if f["alternatives"] else "?"
                multi_hints.append(f"  - 「{f['found']}」→「{alts}」  上下文: …{f.get('context','')}…")
        flag_lines = ["## 已知可能混淆的模式（结合上下文判断，不确定则不改）"]
        if single_hints:
            flag_lines.append("【同音字】")
            for h in single_hints.values():
                flag_lines.append(f"  - {h}")
        if multi_hints:
            flag_lines.append("【具体位置】")
            flag_lines.extend(multi_hints)
        hints_block = "\n".join(flag_lines) + "\n\n"
    else:
        hints_block = ""

    seeds = [seed.strip() for seed in (episode_seeds or []) if seed.strip()]
    seeds_block = ""
    if seeds:
        seeds_block = (
            "## 本期人工确认的实体写法\n"
            + "、".join(seeds)
            + "\n\n这些写法是拼写参考，不是盲目替换规则。只有上下文确实指向该实体时，才把 ASR 变体改成这里的写法；不要改动无关的同音词。\n\n"
        )

    return f"""你是 Qwen3-ASR 字幕纠错助手。本频道内容以中文为主，话题涵盖职场、AI、投资、创业。

## 任务
找出并修正 ASR 语音识别造成的错别字：
- 同音字混淆（如「刘佳」→「刘嘉」，「沉浮」→「臣服」）
- 英文品牌/术语拼写错误（如「Superlillian」→「Superlinear」）
- 人名/公司名/产品名实体错误，并做全文一致性统一。重点检查嘉宾的公司、产品、头衔：ASR 常把不熟悉的英文名听成常见词或人名（真实案例：嘉宾任职的公司「Gen」被转成「Jan」和「Jane」两种写法）。先从上下文推断正确实体名，再把全文所有变体统一成同一写法

## 覆盖要求
从第一段扫到最后一段，不要扫到前半就停。一两个小时的视频通常有几十处可修正的错误；如果你只找到个位数，大概率是没扫完，回头再扫一遍。判断标准不变：每一处都要有上下文依据，宁可漏改不要误改——但「漏改」指不确定的不改，不是没看到。

## 绝对禁止
- 删除/增加实词（名词、动词、形容词）
- 修改语气词/副词（其实、应该、可能、非常、然后等）
- 同义词替换、重新措辞
- 删除重复的短语（口语重启，如「那天我去，那天我去参加」是真实语音）
- 修改已经正确的数字格式

{seeds_block}{hints_block}## 字幕原文

{srt_text}
## 输出格式

输出一个 JSON 数组，每项：{{"chunk_idx": 0, "original": "需修改的最短子字符串（1-8字）", "corrected": "修正后", "reason": "简短原因"}}
- chunk_idx 必须使用字幕原文里方括号中的 0-based 编号；original 必须精确存在于该 chunk
- 同一个错误出现在多个 chunk 时，逐个列出 chunk_idx；不要要求程序做全文盲替换
- 不确定时不输出（宁可漏改，不要误改）
- 没有需要修改的则输出 []
- 只输出 JSON 数组，不要其他内容"""


def call_codex_for_corrections(
    chunks: list[dict],
    flags: list[dict],
    episode_seeds: list[str] | None = None,
    model: str | None = DEFAULT_CODEX_MODEL,
    timeout: int = 900,
) -> tuple[Any, int]:
    """
    文件响应模式：将全文 SRT + 候选词提示写入临时文件，
    让 Codex 将 JSON 修正数组写入另一临时文件，Python 读取并返回。

    Returns:
        (parsed, api_errors)。调用失败或回复无法解析成 JSON 数组时返回
        ([], 1) 并打印真实原因——失败不能伪装成「没有需要修正的内容」，
        那会让上游看到 corrections=0 而误判成功。
    """
    prompt = build_correction_prompt(chunks, flags, episode_seeds=episode_seeds)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
        encoding="utf-8", prefix="kdb_corrections_",
    ) as f:
        f.write("[]")  # 初始化为空数组，确保文件存在
        corrections_file = Path(f.name)

    try:
        call_codex_file_based(prompt, corrections_file, model=model, timeout=timeout)
        raw = corrections_file.read_text(encoding="utf-8").strip()
        return parse_llm_response(raw), 0
    except Exception as e:
        print(
            f"  ✗ Codex 调用或响应解析失败: {type(e).__name__}: {str(e)[:300]}",
            flush=True,
        )
        return [], 1
    finally:
        corrections_file.unlink(missing_ok=True)


def parse_llm_response(raw: str) -> list[dict]:
    stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    stripped = re.sub(r"\s*```$", "", stripped.strip(), flags=re.MULTILINE).strip()
    candidates = [stripped]
    embedded = re.search(r"(\[.*\])", stripped, re.DOTALL)
    if embedded and embedded.group(1) != stripped:
        candidates.append(embedded.group(1))

    parse_errors: list[str] = []
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as error:
            parse_errors.append(str(error))
            continue
        if not isinstance(parsed, list):
            raise ValueError(
                f"校对模型回复必须是 JSON 数组，实际是 {type(parsed).__name__}"
            )
        if not all(isinstance(item, dict) for item in parsed):
            raise ValueError("校对模型 JSON 数组中的每一项都必须是对象")
        for position, item in enumerate(parsed):
            chunk_idx = item.get("chunk_idx")
            if isinstance(chunk_idx, bool) or not isinstance(chunk_idx, int):
                raise ValueError(f"校对模型第 {position + 1} 项缺少整数 chunk_idx")
            if not isinstance(item.get("original"), str) or not item["original"]:
                raise ValueError(f"校对模型第 {position + 1} 项缺少 original")
            if not isinstance(item.get("corrected"), str) or not item["corrected"]:
                raise ValueError(f"校对模型第 {position + 1} 项缺少 corrected")
        return parsed

    detail = parse_errors[-1] if parse_errors else "没有 JSON 数组"
    raise ValueError(f"无法解析校对模型回复：{detail}")


# ── 验证层 ────────────────────────────────────────────────────────────────────

def _edit_distance_approx(a: str, b: str) -> int:
    """Exact Levenshtein distance; strings reaching validation are at most 30 chars."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for row, char_a in enumerate(a, 1):
        current = [row]
        for column, char_b in enumerate(b, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (char_a != char_b),
                )
            )
        previous = current
    return previous[-1]


def _limit_by_edit_budget(corrections: list[dict], total_chars: int) -> list[dict]:
    """Keep model order while enforcing the advertised aggregate edit budget."""
    budget = max(15, int(max(total_chars, 1) * MAX_EDIT_RATIO))
    accepted: list[dict] = []
    used = 0
    for correction in corrections:
        cost = max(len(correction["original"]), len(correction["corrected"]))
        if used + cost > budget:
            continue
        accepted.append(correction)
        used += cost
    return accepted


def _extract_minimal(orig: str, corr: str, flag_patterns: set) -> tuple[str, str] | None:
    if orig == corr or not orig or not corr:
        return None
    for pat in sorted(flag_patterns, key=len, reverse=True):
        pos = orig.find(pat)
        if pos == -1:
            continue
        prefix = orig[:pos]
        suffix = orig[pos + len(pat):]
        if corr.startswith(prefix) and (not suffix or corr.endswith(suffix)):
            end = len(corr) - len(suffix) if suffix else len(corr)
            corr_pat = corr[len(prefix):end]
            if corr_pat and corr_pat != pat:
                return pat, corr_pat
    return None


def validate_corrections(parsed: Any, chunk_texts: list[str], flags: list[dict]) -> list[dict]:
    full_text = "\n".join(chunk_texts)
    flags_by_chunk: dict[int, dict[str, set[str]]] = {}
    for flag in flags:
        flags_by_chunk.setdefault(flag["chunk_idx"], {})[flag["found"]] = set(
            flag.get("alternatives", [])
        )

    items: list = []
    if isinstance(parsed, dict):
        items = parsed.get("flagged", []) + parsed.get("extra", [])
    elif isinstance(parsed, list):
        items = parsed

    corrections = []
    for item in items:
        chunk_idx = item.get("chunk_idx")
        if (
            isinstance(chunk_idx, bool)
            or not isinstance(chunk_idx, int)
            or not 0 <= chunk_idx < len(chunk_texts)
        ):
            continue
        orig = item.get("original") or item.get("found", "")
        corr = item.get("corrected", "")
        if item.get("action") == "KEEP":
            continue
        if not orig or not corr or orig == corr:
            continue
        if orig not in chunk_texts[chunk_idx]:
            continue
        flag_options = flags_by_chunk.get(chunk_idx, {})
        flag_patterns = set(flag_options)
        if orig not in flag_patterns:
            minimal = _extract_minimal(orig, corr, flag_patterns)
            if minimal and minimal[0] in chunk_texts[chunk_idx]:
                orig, corr = minimal
            else:
                continue
        alternatives = flag_options.get(orig, set())
        if alternatives and corr not in alternatives:
            continue
        if len(orig) > 6:
            continue
        if len(corr) - len(orig) > 2:
            continue
        if orig not in _FORMAT_PAT_SET and _edit_distance_approx(orig, corr) > 4:
            continue
        corrections.append(
            {"chunk_idx": chunk_idx, "original": orig, "corrected": corr}
        )

    return _limit_by_edit_budget(corrections, len(full_text))


def merge_corrections(
    strict: list[dict], loose: list[dict], chunk_texts: list[str]
) -> list[dict]:
    """
    v4 把候选词扫描和全文扫描合并成了单次 Codex 调用，所以两个验证器都必须生效：
    - validate_corrections：已知易混词，带 _extract_minimal 收窄
    - validate_corrections_full_scan：本期新出现的人名/品牌/实体

    只跑前者会把后者的结果全部丢掉——candidates 没命中时 flag_patterns 为空集，
    每一条修正都会被 `orig not in flag_patterns` 挡下，最终恒为 corrections=0。
    """
    merged: list[dict] = []
    seen: set = set()
    for group in (strict, loose):
        for c in group:
            key = (c["chunk_idx"], c["original"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)

    # 两个验证器各自限过改动量，合并后再统一兜一次
    full_text = "\n".join(chunk_texts)
    return _limit_by_edit_budget(merged, len(full_text))


def apply_corrections(
    chunks: list[dict], corrections: list[dict]
) -> tuple[list[dict], int]:
    """
    只在模型明确给出的 chunk 中应用修正。即使同一字符串在全文重复，也不能
    因一个位置的判断改掉其他语境；需要统一实体时，模型必须逐个列出 chunk_idx。

    Returns:
        (chunks, replacements) —— replacements 是实际替换处数，不是修正条数
    """
    result = [dict(c) for c in chunks]
    replacements = 0
    for corr in corrections:
        chunk_idx = corr.get("chunk_idx")
        if (
            isinstance(chunk_idx, bool)
            or not isinstance(chunk_idx, int)
            or not 0 <= chunk_idx < len(result)
        ):
            continue
        orig, corrected = corr["original"], corr["corrected"]
        hits = result[chunk_idx]["text"].count(orig)
        if hits:
            result[chunk_idx]["text"] = result[chunk_idx]["text"].replace(
                orig, corrected
            )
            replacements += hits
    return result, replacements


def validate_corrections_full_scan(
    parsed: Any,
    chunk_texts: list[str],
    excluded_flag_patterns: dict[int, set[str]] | None = None,
) -> list[dict]:
    """全文扫描的验证器：比候选词验证器更宽松（不要求 original 在 flag_patterns 里）"""
    full_text = "\n".join(chunk_texts)
    items: list = parsed if isinstance(parsed, list) else []

    corrections = []
    seen_locations: set = set()
    for item in items:
        chunk_idx = item.get("chunk_idx")
        if (
            isinstance(chunk_idx, bool)
            or not isinstance(chunk_idx, int)
            or not 0 <= chunk_idx < len(chunk_texts)
        ):
            continue
        orig = item.get("original", "")
        corr = item.get("corrected", "")
        if not orig or not corr or orig == corr:
            continue
        if orig not in chunk_texts[chunk_idx]:
            continue
        if any(
            pattern in orig or orig in pattern
            for pattern in (excluded_flag_patterns or {}).get(chunk_idx, set())
        ):
            # 已知候选必须只走带 reviewed alternatives 的 strict validator；
            # 不能在 strict 拒绝后从宽松全文扫描路径重新进入。
            continue
        location = (chunk_idx, orig)
        if location in seen_locations:
            continue
        # 最长 8 字（允许英文术语稍长一些）
        if len(orig) > 8 and not re.search(r"[A-Za-z]", orig):
            continue
        if len(orig) > 30:
            continue
        # 只接受局部纠错；大幅扩写和删词都拒绝。
        if abs(len(corr) - len(orig)) > 3:
            continue
        # 不改纯数字
        if orig.isdigit() or corr.isdigit():
            continue
        distance = _edit_distance_approx(orig, corr)
        if re.search(r"[A-Za-z]", orig):
            latin_limit = max(4, (max(len(orig), len(corr)) + 2) // 3)
            if distance > latin_limit:
                continue
        elif distance > 3:
            continue
        seen_locations.add(location)
        corrections.append(
            {"chunk_idx": chunk_idx, "original": orig, "corrected": corr}
        )

    # 批内总改动量上限
    return _limit_by_edit_budget(corrections, len(full_text))


# ── 主校对流程 ─────────────────────────────────────────────────────────────────

def correct_file(
    qwen_path: Path,
    episode_seeds: list[str] | None = None,
    model: str | None = DEFAULT_CODEX_MODEL,
    timeout: int = 900,
    verbose: bool = False,
    stats: dict | None = None,
) -> Path | None:
    """
    对单个 .qwen.srt 文件进行校对，生成 .corrected.srt。

    Args:
        qwen_path: .qwen.srt 文件路径
        episode_seeds: 本期嘉宾名、品牌名等（如 ["刘嘉", "Superlinear Academy"]）
        model: Codex 模型；None 表示使用 Codex CLI 默认配置
        timeout: Codex 全文校对超时秒数；长视频默认 900 秒
        verbose: 是否打印详细日志
        stats: 传入一个 dict 则写入本次统计（fmt/flags/corrections/api_errors），
               供上游做质量门判断——corrections 指 LLM 修正数，不含规则层的 fmt

    Raises:
        CodexUnavailableError: codex CLI 不在 PATH 上（此时不写任何输出文件）
    """
    ensure_codex_available()

    if not qwen_path.exists():
        print(f"  错误：找不到 {qwen_path}")
        return None

    seeds = episode_seeds or []
    out_stem = qwen_path.name.replace(".qwen.srt", "")
    output_path = qwen_path.parent / f"{out_stem}.corrected.srt"

    vocab = load_vocab()
    candidates = build_candidates(vocab)

    chunks = parse_srt(qwen_path)
    if not chunks:
        print(f"  错误：SRT 解析失败 {qwen_path.name}")
        return None

    print(f"  {qwen_path.name}  ({len(chunks)} 条)", flush=True)

    # ── 步骤 1：格式规范化（规则直接执行，不走 LLM）──────────────────────────
    chunks, fmt_count = apply_format_rules(chunks)
    if fmt_count:
        print(f"  格式规范化: {fmt_count} 处", flush=True)

    # ── 步骤 2：候选词扫描 + Codex CLI 全文校对（合并为单次调用）─────────────
    all_flags = scan_flags(chunks, candidates)
    total_flags = len(all_flags)

    parsed, api_errors = call_codex_for_corrections(
        chunks,
        all_flags,
        episode_seeds=seeds,
        model=model,
        timeout=timeout,
    )
    if stats is not None:
        stats.update({"fmt": fmt_count, "flags": total_flags,
                      "corrections": 0, "api_errors": api_errors})
    if api_errors:
        # 单次调用即全部覆盖，失败等于零覆盖。此时产出 corrected.srt 就是把
        # 未精校字幕当成已精校，宁可不产出，让上游据此停下。
        print(f"  ✗ 校对失败  fmt={fmt_count} flags={total_flags} "
              f"corrections=0条/0处 api_errors={api_errors} "
              f"→ 未产出 {output_path.name}", flush=True)
        return None

    chunk_texts = [c["text"] for c in chunks]
    flagged_patterns_by_chunk: dict[int, set[str]] = {}
    for flag in all_flags:
        flagged_patterns_by_chunk.setdefault(flag["chunk_idx"], set()).add(
            flag["found"]
        )
    corrs = merge_corrections(
        validate_corrections(parsed, chunk_texts, all_flags),
        validate_corrections_full_scan(
            parsed,
            chunk_texts,
            excluded_flag_patterns=flagged_patterns_by_chunk,
        ),
        chunk_texts,
    )
    corrected, replacements = apply_corrections(list(chunks), corrs)
    total_corrections = len(corrs)
    if stats is not None:
        stats["corrections"] = total_corrections
        stats["replacements"] = replacements

    # ── 步骤 4：种子词落地情况（供人工确认）──────────────────────────────────
    # 实体一致性由 prompt 要求 Codex 逐个列出需要修改的 chunk，再按位置落地；
    # 这里只报告 seeds 的出现次数，不再做二次猜测或全文盲替换。
    if seeds:
        full_text = " ".join(c["text"] for c in corrected)
        for seed in seeds:
            cnt = full_text.count(seed)
            if cnt:
                print(f"  ✓ 种子词「{seed}」在全文出现 {cnt} 次", flush=True)
            else:
                print(f"  ⚠ 种子词「{seed}」在全文未找到（可能转录形式不同）", flush=True)

    write_srt(corrected, output_path)
    print(f"  ✓ 完成  fmt={fmt_count} flags={total_flags} "
          f"corrections={total_corrections}条/{replacements}处 api_errors={api_errors} "
          f"→ {output_path.name}", flush=True)
    return output_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="字幕校对 v4（Codex CLI 文件响应模式）")
    parser.add_argument("qwen_srt", help=".qwen.srt 文件路径")
    parser.add_argument("--seeds", nargs="*", default=[],
                        help="本期嘉宾名/术语（空格分隔）")
    parser.add_argument(
        "--model",
        default=DEFAULT_CODEX_MODEL,
        help="Codex CLI 模型；不传则使用 Codex 默认配置",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Codex 全文校对超时秒数（默认 900；长视频不要低于此值）",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    try:
        result = correct_file(
            Path(args.qwen_srt),
            episode_seeds=args.seeds,
            model=args.model,
            timeout=args.timeout,
            verbose=args.verbose,
        )
    except CodexUnavailableError as e:
        print(f"\n{'='*55}", file=sys.stderr)
        print(f"✗ 字幕精校无法运行\n\n{e}", file=sys.stderr)
        print(f"{'='*55}", file=sys.stderr)
        sys.exit(1)

    if not result:
        sys.exit(1)
    print(f"\n输出: {result}")


if __name__ == "__main__":
    main()
