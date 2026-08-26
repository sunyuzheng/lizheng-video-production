#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mlx-qwen3-asr CLI 解析顺序回归测试。"""

import sys
import unittest
import unittest.mock
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import process_video  # noqa: E402


class TestAsrCliResolution(unittest.TestCase):
    def test_active_python_environment_precedes_path(self):
        python = "/tmp/active-venv/bin/python"
        active_cli = "/tmp/active-venv/bin/mlx-qwen3-asr"

        with unittest.mock.patch.object(
            process_video.sys, "executable", python
        ), unittest.mock.patch.object(
            process_video.shutil, "which", return_value=active_cli
        ) as which:
            self.assertEqual(process_video._resolve_asr_cli(), active_cli)

        which.assert_called_once_with(active_cli)

    def test_path_is_used_when_active_environment_has_no_cli(self):
        python = "/tmp/active-venv/bin/python"
        path_cli = "/usr/local/bin/mlx-qwen3-asr"

        with unittest.mock.patch.object(
            process_video.sys, "executable", python
        ), unittest.mock.patch.object(
            process_video.shutil, "which", side_effect=[None, path_cli]
        ) as which:
            self.assertEqual(process_video._resolve_asr_cli(), path_cli)

        self.assertEqual(
            which.call_args_list,
            [
                unittest.mock.call("/tmp/active-venv/bin/mlx-qwen3-asr"),
                unittest.mock.call("mlx-qwen3-asr"),
            ],
        )

    def test_homebrew_is_the_final_fallback(self):
        python = "/tmp/active-venv/bin/python"
        homebrew_cli = "/opt/homebrew/bin/mlx-qwen3-asr"

        with unittest.mock.patch.object(
            process_video.sys, "executable", python
        ), unittest.mock.patch.object(
            process_video.shutil, "which", side_effect=[None, None, homebrew_cli]
        ) as which:
            self.assertEqual(process_video._resolve_asr_cli(), homebrew_cli)

        self.assertEqual(
            which.call_args_list,
            [
                unittest.mock.call("/tmp/active-venv/bin/mlx-qwen3-asr"),
                unittest.mock.call("mlx-qwen3-asr"),
                unittest.mock.call(homebrew_cli),
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
