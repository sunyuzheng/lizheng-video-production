import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ArticleEditorialPrinciplesTests(unittest.TestCase):
    def test_reference_teaches_positive_editorial_judgment(self):
        text = (
            ROOT / "skill" / "references" / "article-editorial-principles.md"
        ).read_text(encoding="utf-8")
        for idea in (
            "对材料负责",
            "知道自己在对谁说话",
            "substance 在前",
            "彼此咬合",
            "显出轻重",
            "思想纹理",
        ):
            self.assertIn(idea, text)
        self.assertIn("不是禁句表", text)
        self.assertIn("不是逐项打勾", text)

    def test_generation_prompt_keeps_judgment_open(self):
        from tools.generate_article import ARTICLE_INSTRUCTION, STYLE_BRIEF

        prompt = STYLE_BRIEF + ARTICLE_INSTRUCTION
        self.assertIn("智识结构", prompt)
        self.assertIn("真实读者", prompt)
        self.assertIn("可以不整齐", prompt)
        self.assertIn("以三个视角重读", prompt)
        self.assertNotIn("文章发布门（强制）", prompt)
        self.assertNotIn("命中即拒绝", prompt)


if __name__ == "__main__":
    unittest.main()
