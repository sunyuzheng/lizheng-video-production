import tempfile
import unittest
from pathlib import Path

from tools import generate_highlights


class HighlightPromptPrinciplesTests(unittest.TestCase):
    def test_appended_editor_highlights_keep_locator_timestamps(self) -> None:
        srt_text = """1
00:00:00,000 --> 00:00:10,000
正常开头

2
00:02:00,000 --> 00:02:10,000
正文已经超过一分钟

3
00:04:00,000 --> 00:04:10,000
正文继续

4
00:00:03,000 --> 00:00:08,000
编辑者追加的开场高光
"""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "episode.final.srt"
            source.write_text(srt_text, encoding="utf-8")
            extracted = generate_highlights.extract_appended_highlights(source)

        self.assertIn("[00:03] 编辑者追加的开场高光", extracted)

    def test_prompts_keep_lenses_without_turning_them_into_gates(self) -> None:
        combined = (
            generate_highlights.HIGHLIGHTS_FROM_ACTUAL
            + generate_highlights.HIGHLIGHTS_FROM_SCAN
        )
        self.assertNotIn("两个都是 yes 才是强力候选", combined)
        self.assertNotIn("只用原话，不改写不总结", combined)
        self.assertNotIn("输出 **6-8 段**", combined)
        self.assertIn("定位原话", combined)


if __name__ == "__main__":
    unittest.main()
