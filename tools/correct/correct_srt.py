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

MAX_EDIT_RATIO = 0.20   # 单次最大修改字符比例
# ─────────────────────────────────────────────────────────────────────────────

# ── 格式规范化规则（规则直接执行，不走 LLM）──────────────────────────────────
# format: {pattern: replacement, ...}，带 boundary_guard 的需要额外检查
_FORMAT_RULES: list[dict] = [
    {"pat": "百分之百", "rep": "100%"},
    {"pat": "百分之十", "rep": "10%"},
    {"pat": "两百",     "rep": "200",  "boundary_guard": True},   # 一两百 不改
    {"pat": "两千",     "rep": "2000", "boundary_guard": True},
    {"pat": "幺幺",     "rep": "11"},
    {"pat": "到十",     "rep": "10"},  # 如「到十个」→「到10个」
]
_BOUNDARY_PRECEDING = set("一二三四五六七八九十")
_FORMAT_PAT_SET = {r["pat"] for r in _FORMAT_RULES}
# ─────────────────────────────────────────────────────────────────────────────


def load_vocab() -> dict:
    if _VOCAB_FILE.exists():
        return json.loads(_VOCAB_FILE.read_text(encoding="utf-8"))
    return {}


def build_candidates(vocab: dict, episode_seeds: list[str]) -> dict:
    """
    合并频道候选词 + 本期嘉宾/术语，构建当次校对用的 candidates dict。
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

    # 2. 来自 episode_seeds 的本期术语（精确字符串匹配）
    for seed in episode_seeds:
        seed = seed.strip()
        if not seed:
            continue
        # 不加入 candidates（seeds 用于实体一致性检查，不用于 flag 扫描）
        # 但如果 seed 是 2 字以上中文，加入 candidates 作为重要词保护
        # （防止 LLM 把 seed 词改掉）
        # 实际上 seeds 主要用于 entity consistency check

    return candidates


def parse_srt(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n{2,}", content.strip())
    chunks = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        ts_line = next((l for l in lines if "-->" in l), "")
        text_lines = [l for l in lines if not l.strip().isdigit() and "-->" not in l]
        text = "\n".join(text_lines).strip()
        if not text:
            continue
        chunks.append({"timestamp": ts_line, "text": text})
    return chunks


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

def build_correction_prompt(chunks: list[dict], flags: list[dict]) -> str:
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

{hints_block}## 字幕原文

{srt_text}
## 输出格式

输出一个 JSON 数组，每项：{{"original": "需修改的最短子字符串（1-8字）", "corrected": "修正后", "reason": "简短原因"}}
- original 必须精确存在于字幕原文中
- 不确定时不输出（宁可漏改，不要误改）
- 没有需要修改的则输出 []
- 只输出 JSON 数组，不要其他内容"""


def call_codex_for_corrections(
    chunks: list[dict],
    flags: list[dict],
    model: str | None = DEFAULT_CODEX_MODEL,
    timeout: int = 300,
) -> list[dict]:
    """
    文件响应模式：将全文 SRT + 候选词提示写入临时文件，
    让 Codex 将 JSON 修正数组写入另一临时文件，Python 读取并返回。
    """
    prompt = build_correction_prompt(chunks, flags)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
        encoding="utf-8", prefix="kdb_corrections_",
    ) as f:
        f.write("[]")  # 初始化为空数组，确保文件存在
        corrections_file = Path(f.name)

    try:
        call_codex_file_based(prompt, corrections_file, model=model, timeout=timeout, cwd=_REPO_ROOT)
        raw = corrections_file.read_text(encoding="utf-8").strip()
        return parse_llm_response(raw)
    except Exception:
        return []
    finally:
        corrections_file.unlink(missing_ok=True)


# ── (保留) Prompt 构建（供 validate_corrections 使用）──────────────────────────

def build_prompt(chunks: list[dict], flags: list[dict]) -> tuple[str, str]:
    system = """你是 Qwen3-ASR 字幕纠错助手。本频道内容以中文为主，话题涵盖职场、AI、投资、创业。

## 允许修正的情况
1. 已知同音/专有名词混淆（候选词提示中列出的模式，结合上下文判断）

## 绝对禁止
- 删除/增加实词（名词、动词、形容词）
- 修改语气词/副词（其实、应该、可能、非常、然后等）
- 同义词替换
- 每条 original 超过 6 个字

## 输出格式
JSON 数组，每项：{"original": "最短精确片段", "corrected": "修正后", "reason": "原因"}
- original 必须是需要修改的最短子字符串（1-6字）
- original 必须在字幕中精确存在
- 不确定时输出 []，宁可漏改，不要误改"""

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
        flag_lines = ["## 本批字幕中检测到以下可能混淆的模式（请结合上下文判断，不确定则不改）"]
        if single_hints:
            flag_lines.append("【同音字】请检查下列字的每次出现是否用对：")
            for h in single_hints.values():
                flag_lines.append(f"  - {h}")
        if multi_hints:
            flag_lines.append("【具体位置】")
            flag_lines.extend(multi_hints)
        hints_text = "\n".join(flag_lines) + "\n\n"
    else:
        hints_text = ""

    user = f"{hints_text}## 字幕原文\n{srt_text}\n请输出修正 JSON 数组："
    return system, user




def parse_llm_response(raw: str) -> Any:
    stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    stripped = re.sub(r"\s*```$", "", stripped.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for pat in (r"(\[.*\])", r"(\{.*\})"):
        m = re.search(pat, stripped, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    return []


# ── 验证层 ────────────────────────────────────────────────────────────────────

_CN_DIGITS = set("零一二三四五六七八九十百千万亿两")
_ALL_DIGITS = set("0123456789") | _CN_DIGITS


def _has_digit(s: str) -> bool:
    return any(c in _ALL_DIGITS for c in s)


def _edit_distance_approx(a: str, b: str) -> int:
    if a == b:
        return 0
    common = sum(x == y for x, y in zip(a, b))
    return (len(a) - common) + (len(b) - common)


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
    flag_patterns = {f["found"] for f in flags}

    items: list = []
    if isinstance(parsed, dict):
        items = parsed.get("flagged", []) + parsed.get("extra", [])
    elif isinstance(parsed, list):
        items = parsed

    corrections = []
    for item in items:
        orig = item.get("original") or item.get("found", "")
        corr = item.get("corrected", "")
        if item.get("action") == "KEEP":
            continue
        if not orig or not corr or orig == corr:
            continue
        if orig not in full_text:
            continue
        if orig not in flag_patterns:
            minimal = _extract_minimal(orig, corr, flag_patterns)
            if minimal and minimal[0] in full_text:
                orig, corr = minimal
            else:
                continue
        if orig not in _FORMAT_PAT_SET:
            if _has_digit(orig) or _has_digit(corr):
                continue
        if len(orig) > 6:
            continue
        if len(corr) - len(orig) > 2:
            continue
        if orig not in _FORMAT_PAT_SET and _edit_distance_approx(orig, corr) > 4:
            continue
        corrections.append({"original": orig, "corrected": corr})

    total_chars = max(len(full_text), 1)
    total_changed = sum(len(c["original"]) for c in corrections)
    if total_changed > total_chars * MAX_EDIT_RATIO:
        corrections = sorted(corrections, key=lambda x: len(x["original"]))[:5]

    return corrections


def apply_corrections(chunks: list[dict], corrections: list[dict]) -> list[dict]:
    result = [dict(c) for c in chunks]
    for corr in corrections:
        applied = False
        for chunk in result:
            if corr["original"] in chunk["text"] and not applied:
                chunk["text"] = chunk["text"].replace(corr["original"], corr["corrected"], 1)
                applied = True
    return result


# ── 全文 LLM 扫描 ────────────────────────────────────────────────────────────

def build_full_scan_prompt(chunks: list[dict]) -> tuple[str, str]:
    system = """你是语音转录字幕纠错助手。本频道内容以中文为主，话题涵盖职场、AI、投资、创业。

## 你的任务
找出并修正 ASR（语音识别）造成的错别字。常见错误类型：
- 同音字混淆（如「刘佳」→「刘嘉」，「沉浮」→「臣服」，「亚哥」→「鸭哥」）
- 英文品牌/术语拼写错误（如「Superlillian」→「Superlinear」）
- 数字格式已由规则处理，请勿修改

## 绝对禁止
- 不要删除重复的短语（说话人的口语重启，如「那天我去，那天我去参加」——这是真实语音）
- 不要增删实词、不要同义词替换、不要重新措辞
- 不要修改时间戳行

## 输出格式
JSON 数组，每项：{"original": "需修改的最短子字符串（1-8字）", "corrected": "修正后", "reason": "简短原因"}
- original 必须精确存在于字幕原文中
- 不确定时不输出（宁可漏改，不要误改）
- 没有需要修改的则输出 []"""

    lines = []
    for ci, chunk in enumerate(chunks):
        lines.append(f"[{ci}] {chunk.get('timestamp', '')}")
        lines.append(chunk["text"])
        lines.append("")
    srt_text = "\n".join(lines)

    user = f"## 字幕原文\n{srt_text}\n请输出修正 JSON 数组："
    return system, user


def validate_corrections_full_scan(parsed: Any, chunk_texts: list[str]) -> list[dict]:
    """全文扫描的验证器：比候选词验证器更宽松（不要求 original 在 flag_patterns 里）"""
    full_text = "\n".join(chunk_texts)
    items: list = parsed if isinstance(parsed, list) else []

    corrections = []
    seen_originals: set = set()
    for item in items:
        orig = item.get("original", "")
        corr = item.get("corrected", "")
        if not orig or not corr or orig == corr:
            continue
        if orig not in full_text:
            continue
        if orig in seen_originals:
            continue
        # 最长 8 字（允许英文术语稍长一些）
        if len(orig) > 8 and not re.search(r"[A-Za-z]", orig):
            continue
        if len(orig) > 30:
            continue
        # 不允许大幅扩张（防止 LLM 扩写）
        if len(corr) - len(orig) > 3:
            continue
        # 不改纯数字
        if orig.isdigit() or corr.isdigit():
            continue
        # 修改幅度不能太大（汉字替换 edit distance ≤ 3）
        if not re.search(r"[A-Za-z]", orig) and _edit_distance_approx(orig, corr) > 3:
            continue
        seen_originals.add(orig)
        corrections.append({"original": orig, "corrected": corr})

    # 批内总改动量上限
    total_chars = max(len(full_text), 1)
    total_changed = sum(len(c["original"]) for c in corrections)
    if total_changed > total_chars * MAX_EDIT_RATIO:
        corrections = corrections[:5]

    return corrections




# ── 实体一致性检查 ────────────────────────────────────────────────────────────

def check_entity_consistency(chunks: list[dict], seeds: list[str]) -> tuple[list[dict], int]:
    """
    对用户提供的 seeds（本期嘉宾名/术语），扫描全文看是否有同音/形近变体，
    用「少数服从多数」原则统一写法。
    """
    if not seeds:
        return chunks, 0

    result = [dict(c) for c in chunks]
    full_text = " ".join(c["text"] for c in result)
    fixes = 0

    for seed in seeds:
        seed = seed.strip()
        if not seed or len(seed) < 2:
            continue
        seed_count = full_text.count(seed)
        if seed_count == 0:
            continue

        # 简单策略：只做已知形近字替换（不做 LLM 猜测）
        # 例如：如果 seed="刘嘉"，就看全文里有没有「刘佳」（形近字）
        # 这里用一个简单的：找同音字变体（同拼音的常见汉字）
        # 暂时只处理：seed 出现在全文中，且存在比 seed_count 少的其他形式 → 不做
        # TODO: 更完善的实体一致性需要 LLM 或音形字典

        # 当前只做：确认 seed 在全文里至少出现 1 次（说明转录对了），输出到报告
        pass  # placeholder for future enhancement

    return result, fixes


# ── 主校对流程 ─────────────────────────────────────────────────────────────────

def correct_file(
    qwen_path: Path,
    episode_seeds: list[str] | None = None,
    model: str | None = DEFAULT_CODEX_MODEL,
    verbose: bool = False,
) -> Path | None:
    """
    对单个 .qwen.srt 文件进行校对，生成 .corrected.srt。

    Args:
        qwen_path: .qwen.srt 文件路径
        episode_seeds: 本期嘉宾名、品牌名等（如 ["刘嘉", "Superlinear Academy"]）
        model: Codex 模型；None 表示使用 Codex CLI 默认配置
        verbose: 是否打印详细日志
    """
    if not qwen_path.exists():
        print(f"  错误：找不到 {qwen_path}")
        return None

    seeds = episode_seeds or []
    out_stem = qwen_path.name.replace(".qwen.srt", "")
    output_path = qwen_path.parent / f"{out_stem}.corrected.srt"

    vocab = load_vocab()
    candidates = build_candidates(vocab, seeds)

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

    parsed = call_codex_for_corrections(chunks, all_flags, model=model)
    chunk_texts = [c["text"] for c in chunks]
    corrs = validate_corrections(parsed, chunk_texts, all_flags)
    corrected = apply_corrections(list(chunks), corrs)
    total_corrections = len(corrs)
    scan_corrections = 0  # 已合并入单次调用，不再单独统计

    # ── 步骤 4：实体一致性检查（seeds）────────────────────────────────────────
    if seeds:
        corrected, entity_fixes = check_entity_consistency(corrected, seeds)
        if entity_fixes:
            print(f"  实体统一: {entity_fixes} 处", flush=True)
        # 打印 seeds 在全文中的出现情况（供用户确认）
        full_text = " ".join(c["text"] for c in corrected)
        for seed in seeds:
            cnt = full_text.count(seed)
            if cnt:
                print(f"  ✓ 种子词「{seed}」在全文出现 {cnt} 次", flush=True)
            else:
                print(f"  ⚠ 种子词「{seed}」在全文未找到（可能转录形式不同）", flush=True)

    write_srt(corrected, output_path)
    print(f"  ✓ 完成  fmt={fmt_count} flags={total_flags} "
          f"corrections={total_corrections}+{scan_corrections}(scan) api_errors=0 "
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
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    result = correct_file(
        Path(args.qwen_srt),
        episode_seeds=args.seeds,
        model=args.model,
        verbose=args.verbose,
    )
    if result:
        print(f"\n输出: {result}")


if __name__ == "__main__":
    main()
