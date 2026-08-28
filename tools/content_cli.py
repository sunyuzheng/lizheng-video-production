#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe text-generation routing for content assets.

Codex is the primary engine. Claude is an optional fallback. Both direct
adapters run without model tools; Python owns the output file.
"""

import os
import subprocess
from pathlib import Path

from tools.claude_cli import _call_claude_once
from tools.codex_cli import call_codex_file_based

DEFAULT_CONTENT_MODEL: str | None = (
    os.environ.get("LIZHENG_CODEX_CONTENT_MODEL")
    or os.environ.get("LIZHENG_CODEX_MODEL")
    or None
)
FALLBACK_CLAUDE_MODEL: str | None = (
    os.environ.get("LIZHENG_CLAUDE_FALLBACK_MODEL")
    or os.environ.get("LIZHENG_CLAUDE_MODEL")
    or None
)


def call_content_file_based(
    prompt: str,
    output_path: Path,
    model: str | None = DEFAULT_CONTENT_MODEL,
    timeout: int = 900,
    fallback: bool = True,
) -> str:
    """Generate one text asset with Codex first and Claude as fallback."""
    try:
        return call_codex_file_based(
            prompt,
            output_path,
            model=model,
            timeout=timeout,
        )
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as primary_error:
        if not fallback:
            raise

        codex_label = model or "CLI 默认配置"
        claude_label = FALLBACK_CLAUDE_MODEL or "CLI 默认配置"
        print(
            f"  ⚠ codex ({codex_label}) 不可用，降级到 claude "
            f"({claude_label}): {str(primary_error)[:200]}",
            flush=True,
        )
        try:
            return _call_claude_once(
                prompt,
                output_path,
                model=FALLBACK_CLAUDE_MODEL,
                timeout=timeout,
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as fallback_error:
            raise RuntimeError(
                "内容生成失败：Codex primary 与 Claude fallback 均不可用。"
                f" Codex: {str(primary_error)[:240]};"
                f" Claude: {str(fallback_error)[:240]}"
            ) from fallback_error
