import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.claude_cli import _call_claude_once


class ClaudeOutputTests(unittest.TestCase):
    def test_stale_output_is_not_accepted_as_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "article.md"
            output_path.write_text("stale", encoding="utf-8")

            with patch(
                "tools.claude_cli.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                with self.assertRaisesRegex(RuntimeError, "未写入输出文件"):
                    _call_claude_once(
                        "prompt",
                        output_path,
                        model="test-model",
                        timeout=1,
                    )

            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
