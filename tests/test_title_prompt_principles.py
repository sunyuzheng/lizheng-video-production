import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import generate_titles


class TitlePromptPrinciplesTests(unittest.TestCase):
    def test_prompts_do_not_reintroduce_old_rigid_packaging_rules(self) -> None:
        combined = "\n".join(
            [
                generate_titles.ROUND0_WITH_HIGHLIGHTS,
                generate_titles.ROUND0_WITHOUT_HIGHLIGHTS,
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

    def test_title_generation_can_ignore_stale_highlights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = root / "episode.article.md"
            content.write_text("CURRENT CONTENT", encoding="utf-8")
            (root / "episode.highlights.md").write_text("STALE", encoding="utf-8")
            captured: dict[str, str] = {}

            def fake_round0(text, highlights, workspace):
                captured["content"] = text
                captured["highlights"] = highlights
                result = workspace / "round0.md"
                result.write_text("round0", encoding="utf-8")
                return result

            with patch.object(generate_titles, "run_round0", side_effect=fake_round0):
                generate_titles.generate_titles(
                    content,
                    stop_at_round=0,
                    discover_highlights=False,
                )

            self.assertEqual(captured["highlights"], "")
            self.assertEqual(captured["content"], "CURRENT CONTENT")


if __name__ == "__main__":
    unittest.main()
