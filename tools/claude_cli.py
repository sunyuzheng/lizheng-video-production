#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件响应模式 Claude CLI 工具

原则（来自 gist.github.com/grapeot/9cbdcf7f26bd1d69a11c39414b54dbe6）：
  - 文件模式 > pipe 模式：Claude 的心理模型是"完成工作并保存"，而非"对话回答"，不会截断
  - 大内容通过文件传递，绕过 CLI 参数长度限制
  - --permission-mode bypassPermissions 用于自动化流水线
"""

import subprocess
import tempfile
from pathlib import Path

DEFAULT_MODEL = "claude-fable-5"
FALLBACK_CODEX_MODEL = "gpt-5.5"


def call_claude_file_based(
    prompt: str,
    output_path: Path,
    model: str = DEFAULT_MODEL,
    timeout: int = 900,
    fallback: bool = True,
) -> str:
    """
    文件响应模式，带降级：优先 Claude（默认 claude-fable-5）；Claude CLI 不存在、
    调用失败、超时或未写出文件时，自动降级到 Codex（gpt-5.5），产物文件约定不变。

    Args:
        prompt:      完整任务描述（包含上下文内容，可以很大）
        output_path: 模型将写入结果的目标文件
        model:       Claude 模型，默认 claude-fable-5
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
        print(
            f"  ⚠ claude ({model}) 不可用，降级到 codex {FALLBACK_CODEX_MODEL}: "
            f"{str(e)[:200]}",
            flush=True,
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
    model: str,
    timeout: int,
) -> str:
    """单次 Claude 调用：prompt 写临时文件，让 Claude 把完整输出写入 output_path。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False,
        encoding="utf-8", prefix="kdb_task_",
    ) as f:
        f.write(prompt)
        task_file = Path(f.name)

    try:
        instruction = (
            f"Read all task instructions and content from {task_file}. "
            f"Write your complete response directly to {output_path}. "
            f"Do not output anything to the terminal — write only to the file."
        )
        result = subprocess.run(
            [
                "claude",
                "--permission-mode", "bypassPermissions",
                "--model", model,
                "-p", instruction,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude 失败 (exit {result.returncode}): {result.stderr[:400]}"
            )
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"Claude 未写入输出文件: {output_path}")

        return output_path.read_text(encoding="utf-8")
    finally:
        task_file.unlink(missing_ok=True)
