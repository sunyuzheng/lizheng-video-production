import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.content_cli import call_content_file_based


class ContentCliRoutingTests(unittest.TestCase):
    def test_codex_is_primary_and_claude_is_not_called_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.md"

            def fake_codex(prompt, output_path, model=None, timeout=900):
                output_path.write_text("from codex", encoding="utf-8")
                return "from codex"

            with (
                patch("tools.content_cli.call_codex_file_based", side_effect=fake_codex) as codex,
                patch("tools.content_cli._call_claude_once") as claude,
            ):
                result = call_content_file_based(
                    "prompt", output, model="codex-choice", timeout=17
                )

        self.assertEqual(result, "from codex")
        codex.assert_called_once_with(
            "prompt", output, model="codex-choice", timeout=17
        )
        claude.assert_not_called()

    def test_claude_is_used_only_after_codex_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.md"

            def fake_claude(prompt, output_path, model=None, timeout=900):
                output_path.write_text("from claude", encoding="utf-8")
                return "from claude"

            with (
                patch(
                    "tools.content_cli.call_codex_file_based",
                    side_effect=RuntimeError("codex unavailable"),
                ) as codex,
                patch("tools.content_cli.FALLBACK_CLAUDE_MODEL", "claude-choice"),
                patch(
                    "tools.content_cli._call_claude_once", side_effect=fake_claude
                ) as claude,
            ):
                result = call_content_file_based("prompt", output, timeout=23)

        self.assertEqual(result, "from claude")
        codex.assert_called_once()
        claude.assert_called_once_with(
            "prompt", output, model="claude-choice", timeout=23
        )

    def test_codex_startup_os_error_uses_claude_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.md"

            def fake_claude(prompt, output_path, model=None, timeout=900):
                output_path.write_text("from claude", encoding="utf-8")
                return "from claude"

            with (
                patch(
                    "tools.content_cli.call_codex_file_based",
                    side_effect=PermissionError("codex is not executable"),
                ),
                patch(
                    "tools.content_cli._call_claude_once", side_effect=fake_claude
                ) as claude,
            ):
                result = call_content_file_based("prompt", output)

        self.assertEqual(result, "from claude")
        claude.assert_called_once()

    def test_fallback_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "tools.content_cli.call_codex_file_based",
                    side_effect=subprocess.TimeoutExpired("codex", 1),
                ),
                patch("tools.content_cli._call_claude_once") as claude,
            ):
                with self.assertRaises(subprocess.TimeoutExpired):
                    call_content_file_based(
                        "prompt", Path(tmp) / "out.md", timeout=1, fallback=False
                    )

        claude.assert_not_called()

    def test_both_failures_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "tools.content_cli.call_codex_file_based",
                    side_effect=RuntimeError("codex auth failed"),
                ),
                patch(
                    "tools.content_cli._call_claude_once",
                    side_effect=RuntimeError("claude auth failed"),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "Codex primary 与 Claude fallback 均不可用"
                ) as error:
                    call_content_file_based("prompt", Path(tmp) / "out.md")

        self.assertIn("codex auth failed", str(error.exception))
        self.assertIn("claude auth failed", str(error.exception))

    def test_claude_startup_os_error_is_reported_with_primary_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "tools.content_cli.call_codex_file_based",
                    side_effect=RuntimeError("codex auth failed"),
                ),
                patch(
                    "tools.content_cli._call_claude_once",
                    side_effect=PermissionError("claude is not executable"),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "Codex primary 与 Claude fallback 均不可用"
                ) as error:
                    call_content_file_based("prompt", Path(tmp) / "out.md")

        self.assertIn("codex auth failed", str(error.exception))
        self.assertIn("claude is not executable", str(error.exception))


if __name__ == "__main__":
    unittest.main()
