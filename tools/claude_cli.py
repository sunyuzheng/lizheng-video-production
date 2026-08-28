#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe, non-interactive Claude CLI text generation.

The model receives the task on stdin, has no tools, and returns text on stdout.
Python owns the output file so a text-generation step never needs filesystem or
shell permissions.
"""

import os
import subprocess
from pathlib import Path

DEFAULT_MODEL: str | None = os.environ.get("LIZHENG_CLAUDE_MODEL") or None
FALLBACK_CODEX_MODEL: str | None = os.environ.get("LIZHENG_CODEX_MODEL") or None


def call_claude_file_based(
    prompt: str,
    output_path: Path,
    model: str | None = DEFAULT_MODEL,
    timeout: int = 900,
    fallback: bool = True,
) -> str:
    """
    安全文本模式，带降级：优先 Claude CLI；Claude CLI 不存在、调用失败、
    超时或未返回文本时，自动降级到 Codex CLI，产物文件约定不变。

    Args:
        prompt:      完整任务描述（包含上下文内容，可以很大）
        output_path: 模型将写入结果的目标文件
        model:       Claude 模型；None 表示使用 Claude CLI 默认配置
        timeout:     subprocess 超时秒数
        fallback:    是否允许降级到 Codex，默认允许

    Returns:
        output_path 写入的内容字符串

    Raises:
        RuntimeError: Claude 调用失败且降级被禁用，或降级后同样失败
    """
    try:
        return _call_claude_once(prompt, output_path, model=model, timeout=timeout)
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        if not fallback:
            raise
        claude_label = model or "CLI 默认配置"
        codex_label = FALLBACK_CODEX_MODEL or "CLI 默认配置"
        print(
            f"  ⚠ claude ({claude_label}) 不可用，降级到 codex ({codex_label}): "
            f"{str(e)[:200]}", flush=True,
        )
        try:
            from tools.codex_cli import call_codex_file_based
        except ImportError:
            from codex_cli import call_codex_file_based

        return call_codex_file_based(
            prompt, output_path, model=FALLBACK_CODEX_MODEL, timeout=timeout
        )


def _call_claude_once(
    prompt: str,
    output_path: Path,
    model: str | None,
    timeout: int,
) -> str:
    """Run Claude once without tools and write its stdout to ``output_path``."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    instruction = (
        "Complete the task using only the instructions and content below. "
        "Return only the requested final answer content, with no preface.\n\n"
        f"{prompt}"
    )
    cmd = [
        "claude",
        "--print",
        "--safe-mode",
        "--tools",
        "",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "text",
    ]
    if model:
        cmd.extend(["--model", model])

    result = subprocess.run(
        cmd,
        input=instruction,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "无错误详情"
        raise RuntimeError(
            f"claude 失败 (exit {result.returncode}): {detail[:400]}"
        )
    if not result.stdout.strip():
        raise RuntimeError("Claude 未返回任何文本")

    output_path.write_text(result.stdout, encoding="utf-8")
    return result.stdout
