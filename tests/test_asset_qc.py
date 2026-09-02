import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.asset_qc import AssetValidationError, validate_title_output, validate_youtube_description
from tools.generate_youtube_description import PROMPT, generate_youtube_description


SRT = """1
00:00:00,000 --> 00:00:12,000
开头

2
00:00:12,000 --> 00:00:30,000
中段

3
00:00:30,000 --> 00:01:00,000
结尾
"""


class YoutubeDescriptionQcTests(unittest.TestCase):
    def test_prompt_leads_with_substance_instead_of_meta_praise(self) -> None:
        self.assertIn("开头直接进入 substance", PROMPT)
        self.assertIn("不要先写", PROMPT)
        self.assertNotIn("他们为什么值得看看", PROMPT)

    def _validate(self, description: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "episode.final.srt"
            srt.write_text(SRT, encoding="utf-8")
            return validate_youtube_description(description, srt)

    def test_valid_chapters_match_subtitle_domain(self) -> None:
        errors = self._validate(
            "这是一段介绍。\n\n章节：\n00:00 开场\n00:15 中段\n00:40 结尾\n"
        )
        self.assertEqual(errors, [])

    def test_requires_zero_strict_order_and_in_range(self) -> None:
        errors = self._validate(
            "介绍\n章节：\n00:05 开场\n00:20 中段\n00:20 重复\n01:20 越界\n"
        )
        combined = "；".join(errors)
        self.assertIn("00:00", combined)
        self.assertIn("未严格递增", combined)
        self.assertIn("超出字幕时域", combined)

    def test_rejects_malformed_lines_after_chapter_marker(self) -> None:
        errors = self._validate(
            "介绍\n章节：\n00:00 开场\n下一章 00:15\n00:30 结尾\n"
        )
        self.assertTrue(any("格式非法" in error for error in errors))

    def test_invalid_rerun_preserves_previous_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "episode.final.srt"
            srt.write_text(SRT, encoding="utf-8")
            delivery = root / "episode.youtube-description.txt"
            delivery.write_text("previous good delivery", encoding="utf-8")

            def fake_call(_prompt, output_path, model=None):
                output_path.write_text(
                    "介绍\n章节：\n00:05 错误开头\n00:20 中段\n00:40 结尾\n",
                    encoding="utf-8",
                )

            with patch(
                "tools.generate_youtube_description.call_content_file_based",
                side_effect=fake_call,
            ):
                with self.assertRaises(AssetValidationError):
                    generate_youtube_description(srt, output_dir=root, stem="episode")

            self.assertEqual(
                delivery.read_text(encoding="utf-8"), "previous good delivery"
            )
            self.assertTrue((root / "episode.youtube-description.invalid.txt").exists())


class TitleQcTests(unittest.TestCase):
    def test_valid_structure(self) -> None:
        text = (
            "## 首选组合\n\n"
            "- **标题：** 一个标题\n"
            "- **封面主文案：** 一句大字\n"
            "- **封面画面：** 一个人物关系\n"
            "- **观众会追问：** 为什么\n"
            "- **视频兑现：** 00:10\n"
            "- **开头衔接：** 用对应高光\n\n"
            "## 备选组合\n\n备选\n\n"
            "## 放弃的方向\n\n放弃\n"
        )
        self.assertEqual(validate_title_output(text), [])

    def test_missing_section_is_explicit(self) -> None:
        errors = validate_title_output("## 首选组合\n- 标题：一个标题")
        self.assertTrue(any("备选组合" in error for error in errors))
        self.assertTrue(any("放弃的方向" in error for error in errors))

    def test_primary_package_requires_click_and_payoff_fields(self) -> None:
        text = (
            "## 首选组合\n\n- 标题：一个标题\n\n"
            "## 备选组合\n\n备选\n\n"
            "## 放弃的方向\n\n放弃\n"
        )
        errors = validate_title_output(text)
        self.assertTrue(any("封面主文案" in error for error in errors))
        self.assertTrue(any("封面画面" in error for error in errors))
        self.assertTrue(any("观众会追问" in error for error in errors))
        self.assertTrue(any("视频兑现" in error for error in errors))
        self.assertTrue(any("开头衔接" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
