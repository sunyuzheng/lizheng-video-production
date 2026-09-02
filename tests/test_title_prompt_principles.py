import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import generate_titles


class TitlePromptPrinciplesTests(unittest.TestCase):
    def test_prompts_do_not_reintroduce_old_rigid_packaging_rules(self) -> None:
        combined = "\n".join(
            [
                generate_titles.TITLE_BRIEF_PROMPT,
                generate_titles.ROUND0_PROMPT,
                generate_titles.ROUND1_PROMPT,
                generate_titles.ROUND2_PROMPT,
            ]
        )
        for stale_rule in (
            "标题不描述高光本身",
            "互补不重复",
            "转发者显得清醒",
            "想要底层框架",
            "不剧透高光",
        ):
            self.assertNotIn(stale_rule, combined)

    def test_independent_review_receives_current_guideline(self) -> None:
        self.assertIn("{guideline}", generate_titles.ROUND1_PROMPT)
        self.assertIn("{content}", generate_titles.ROUND1_PROMPT)
        self.assertIn("{brief}", generate_titles.ROUND1_PROMPT)

    def test_prompts_mine_the_strongest_point_instead_of_summarizing(self) -> None:
        self.assertIn("一个强点可以只来自整期中的一段", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("不需要代表全部内容", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("不要把它翻译成更抽象的行业术语", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("不要给整期写摘要", generate_titles.ROUND0_PROMPT)
        self.assertIn("一个只覆盖强段落", generate_titles.ROUND1_PROMPT)
        self.assertNotIn("看前 → 看后", generate_titles.ROUND0_PROMPT)
        self.assertNotIn("看前 → 看后", generate_titles.ROUND2_PROMPT)

    def test_prompts_treat_title_thumbnail_and_payoff_as_one_package(self) -> None:
        self.assertIn("标题 × 封面组合", generate_titles.ROUND0_PROMPT)
        self.assertIn("观众脑中被打开的那一个问题", generate_titles.ROUND0_PROMPT)
        self.assertIn("缩小后的封面", generate_titles.ROUND1_PROMPT)
        self.assertIn("开头有没有很快证明", generate_titles.ROUND1_PROMPT)
        self.assertIn("## 首选组合", generate_titles.ROUND2_PROMPT)
        self.assertIn("视频兑现", generate_titles.ROUND2_PROMPT)

    def test_prompts_encode_the_real_channel_audience_priority(self) -> None:
        self.assertIn("科技、进步、AI 与个人成长", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("高管、学生和创业者", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("不因此自动服务那个职业的小圈子", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("嘉宾是 VC 时，不要把 VC 从业者当默认受众", generate_titles.ROUND2_PROMPT)

    def test_viewer_transformation_is_only_a_payoff_check(self) -> None:
        self.assertIn("只可用来复核", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("不把它当作选题中心", generate_titles.TITLE_BRIEF_PROMPT)

    def test_brief_artifact_is_named_for_packaging_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)

            def fake_call(prompt, output_path, model):
                output_path.write_text("brief", encoding="utf-8")

            with patch.object(
                generate_titles,
                "call_content_file_based",
                side_effect=fake_call,
            ):
                result = generate_titles.build_title_brief(
                    "source material",
                    "",
                    workspace,
                )

            self.assertEqual(result.name, "packaging_brief.md")
            self.assertEqual(result.read_text(encoding="utf-8"), "brief")

    def test_title_generation_uses_full_content_and_can_ignore_stale_highlights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = root / "episode.article.md"
            source = "CURRENT CONTENT\n" + ("x" * 7000) + "\nTAIL EVIDENCE"
            content.write_text(source, encoding="utf-8")
            (root / "episode.highlights.md").write_text("STALE", encoding="utf-8")
            captured: dict[str, str] = {}

            def fake_brief(text, highlights, workspace):
                captured["content"] = text
                captured["highlights"] = highlights
                result = workspace / "brief.md"
                result.write_text("brief", encoding="utf-8")
                return result

            def fake_round0(brief, workspace):
                result = workspace / "round0.md"
                result.write_text("round0", encoding="utf-8")
                return result

            with (
                patch.object(generate_titles, "build_title_brief", side_effect=fake_brief),
                patch.object(generate_titles, "run_round0", side_effect=fake_round0),
            ):
                generate_titles.generate_titles(
                    content,
                    stop_at_round=0,
                    discover_highlights=False,
                )

            self.assertEqual(captured["highlights"], "")
            self.assertEqual(captured["content"], source)
            self.assertTrue(captured["content"].endswith("TAIL EVIDENCE"))

    def test_srt_source_keeps_evidence_after_the_old_6000_character_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "episode.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:10,000\n"
                + ("前" * 6500)
                + "\n\n2\n00:10:00,000 --> 00:10:05,000\nTAIL EVIDENCE\n",
                encoding="utf-8",
            )

            text = generate_titles.srt_to_text(srt)

            self.assertIn("TAIL EVIDENCE", text)
            self.assertNotIn("已截断", text)


if __name__ == "__main__":
    unittest.main()
