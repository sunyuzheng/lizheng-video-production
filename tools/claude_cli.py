#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe, non-interactive Claude CLI text generation.

The model receives the task on stdin, has no tools, and returns text on stdout.
Python owns the output file so a text-generation step never needs filesystem or
shell permissions. Content routing lives in ``tools.content_cli``; this module
is the direct Claude adapter used by the fallback path.
"""

import os
import subprocess
from pathlib import Path

DEFAULT_MODEL: str | None = os.environ.get("LIZHENG_CLAUDE_MODEL") or None


def call_claude_file_based(
    prompt: str,
    output_path: Path,
    model: str | None = DEFAULT_MODEL,
    timeout: int = 900,
) -> str:
    """Call Claude directly; ordinary content generation uses content_cli."""
    return _call_claude_once(prompt, output_path, model=model, timeout=timeout)


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
