import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import speaker_attribution


class SpeakerAttributionPathTests(unittest.TestCase):
    def test_labeled_output_cannot_alias_input_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "episode.mp4"
            media.write_bytes(b"media")
            labeled = root / "episode.speaker_labeled.srt"
            labeled.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n主持人：原文\n",
                encoding="utf-8",
            )
            argv = [
                "speaker_attribution.py",
                str(media),
                "--srt",
                str(labeled),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(speaker_attribution, "extract_audio") as extract_audio,
            ):
                with self.assertRaises(SystemExit):
                    speaker_attribution.main()

            extract_audio.assert_not_called()
            self.assertIn("主持人：原文", labeled.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
