import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.generate_highlights import generate_highlights


class HighlightDeliverySafetyTests(unittest.TestCase):
    def test_failed_generation_preserves_existing_delivery(self) -> None:
        sample = """1
00:00:00,000 --> 00:00:03,000
第一句
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "episode.final.srt"
            output = root / "episode.highlights.md"
            srt.write_text(sample, encoding="utf-8")
            output.write_text("known-good", encoding="utf-8")

            with patch(
                "tools.generate_highlights.call_claude_file_based",
                side_effect=RuntimeError("both model CLIs failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "both model CLIs failed"):
                    generate_highlights(srt, output_dir=root)

            self.assertEqual(output.read_text(encoding="utf-8"), "known-good")


if __name__ == "__main__":
    unittest.main()
