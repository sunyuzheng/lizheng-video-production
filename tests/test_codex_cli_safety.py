import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.codex_cli import call_codex_file_based


class CodexCliSafetyTests(unittest.TestCase):
    def test_text_fallback_uses_ephemeral_empty_read_only_workspace(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["input"] = kwargs["input"]
            output = Path(command[command.index("-o") + 1])
            output.write_text("generated\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp, patch(
            "tools.codex_cli.subprocess.run", side_effect=fake_run
        ):
            output = Path(tmp) / "result.md"
            result = call_codex_file_based(
                "private transcript", output, model=None, timeout=5
            )

        self.assertEqual(result, "generated\n")
        command = captured["command"]
        self.assertIn("read-only", command)
        self.assertIn("never", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertNotIn("--model", command)
        self.assertIn("private transcript", captured["input"])
        run_cwd = command[command.index("-C") + 1]
        self.assertIn("lizheng-codex-text-", run_cwd)
        self.assertFalse(Path(run_cwd).exists())


if __name__ == "__main__":
    unittest.main()
