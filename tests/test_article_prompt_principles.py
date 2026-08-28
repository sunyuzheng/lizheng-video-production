import unittest


class ArticlePromptPrinciplesTests(unittest.TestCase):
    def test_generation_prompt_has_one_writing_owner_and_evidence_boundary(self):
        from tools.generate_article import ARTICLE_INSTRUCTION, STYLE_BRIEF

        prompt = STYLE_BRIEF + ARTICLE_INSTRUCTION
        self.assertIn("当前唯一主责 writing skill", prompt)
        self.assertIn("逐字稿", prompt)
        self.assertIn("不要新增材料没有支持", prompt)
        self.assertIn("只有 `companion`", prompt)
        self.assertNotIn("文章发布门（强制）", prompt)
        self.assertNotIn("命中即拒绝", prompt)
        self.assertNotIn("三个视角", prompt)


if __name__ == "__main__":
    unittest.main()
