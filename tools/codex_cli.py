#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件响应模式 Codex CLI 工具

用于需要 Codex 判断的自动化步骤，例如字幕精校。调用方把完整任务
作为 stdin 传给 `codex exec`，并用 `--output-last-message` 捕获最终回复。
"""

import subprocess
from pathlib import Path

DEFAULT_CODEX_MODEL: str | None = None


def call_codex_file_based(
    prompt: str,
    output_path: Path,
    model: str | None = DEFAULT_CODEX_MODEL,
    timeout: int = 900,
    cwd: Path | None = None,
) -> str:
    """
    文件响应模式：将 prompt 传给 Codex CLI，并把最终回复写入 output_path。

    Args:
        prompt:      完整任务描述（包含上下文内容，可以很大）
        output_path: Codex 最终回复写入的目标文件
        model:       Codex 模型；None 表示使用 Codex CLI 默认配置
        timeout:     subprocess 超时秒数
        cwd:         Codex CLI 工作目录

    Returns:
        output_path 写入的内容字符串

    Raises:
        RuntimeError: Codex 调用失败或未生成输出文件
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    instruction = (
        "Complete the task using only the instructions and content below. "
        "Return only the requested final answer content, with no preface.\n\n"
        f"{prompt}"
    )

    cmd = [
        "codex",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "read-only",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.extend([
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-rules",
        "--color",
        "never",
        "-C",
        str(cwd or Path.cwd()),
        "-o",
        str(output_path),
    ])
    cmd.append("-")

    result = subprocess.run(
        cmd,
        input=instruction,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"codex 失败 (exit {result.returncode}): {result.stderr[:400]}"
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Codex 未写入输出文件: {output_path}")

    return output_path.read_text(encoding="utf-8")
