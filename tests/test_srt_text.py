import tempfile
import unittest
from pathlib import Path

from tools.srt_text import plain_text_from_srt, timed_text_from_srt


class StrictSrtTextTests(unittest.TestCase):
    def test_malformed_block_is_never_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.srt"
            path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\nBROKEN",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "不会静默忽略"):
                plain_text_from_srt(path)

    def test_appended_highlight_timeline_is_not_duplicated_into_main_text(self) -> None:
        sample = """1
00:00:00,000 --> 00:00:01,000
开场

2
00:02:00,000 --> 00:02:01,000
正文

3
00:00:05,000 --> 00:00:06,000
追加高光
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episode.srt"
            path.write_text(sample, encoding="utf-8")
            plain = plain_text_from_srt(path)
            timed = timed_text_from_srt(path)

        self.assertIn("开场", plain)
        self.assertIn("正文", plain)
        self.assertNotIn("追加高光", plain)
        self.assertNotIn("追加高光", timed)


if __name__ == "__main__":
    unittest.main()
