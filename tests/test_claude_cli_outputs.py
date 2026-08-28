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
                with self.assertRaisesRegex(RuntimeError, "未返回任何文本"):
                    _call_claude_once(
                        "prompt",
                        output_path,
                        model="test-model",
                        timeout=1,
                    )

            self.assertFalse(output_path.exists())

    def test_stdout_is_written_by_python_with_no_model_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "article.md"
            completed = subprocess.CompletedProcess([], 0, "generated\n", "")

            with patch(
                "tools.claude_cli.subprocess.run", return_value=completed
            ) as run:
                result = _call_claude_once(
                    "private transcript",
                    output_path,
                    model=None,
                    timeout=5,
                )

            self.assertEqual(result, "generated\n")
            self.assertEqual(output_path.read_text(encoding="utf-8"), "generated\n")
            command = run.call_args.args[0]
            self.assertNotIn("bypassPermissions", command)
            self.assertNotIn("--model", command)
            self.assertIn("--safe-mode", command)
            self.assertIn("--no-session-persistence", command)
            tools_index = command.index("--tools")
            self.assertEqual(command[tools_index + 1], "")
            self.assertIn("private transcript", run.call_args.kwargs["input"])

    def test_explicit_model_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "tools.claude_cli.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "ok", ""),
        ) as run:
            _call_claude_once(
                "prompt",
                Path(tmp) / "out.txt",
                model="chosen-model",
                timeout=5,
            )

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "chosen-model")

    def test_failure_uses_stdout_when_cli_does_not_write_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "tools.claude_cli.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 1, "Failed to authenticate", ""
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Failed to authenticate"):
                _call_claude_once(
                    "prompt",
                    Path(tmp) / "out.txt",
                    model=None,
                    timeout=5,
                )


if __name__ == "__main__":
    unittest.main()
