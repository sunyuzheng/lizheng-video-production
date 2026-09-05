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
                generate_titles.ROUND1_INDEPENDENT_PROMPT,
                generate_titles.ROUND1_COMPARE_PROMPT,
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

    def test_independent_challenger_is_not_anchored_by_round0_or_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            brief = workspace / "brief.md"
            round0 = workspace / "round0.md"
            brief.write_text("BRIEF_SENTINEL", encoding="utf-8")
            round0.write_text("ROUND0_SENTINEL", encoding="utf-8")
            prompts: list[str] = []

            def fake_call(prompt, output_path, model):
                prompts.append(prompt)
                output_path.write_text(
                    "INDEPENDENT_SENTINEL" if len(prompts) == 1 else "REVIEW",
                    encoding="utf-8",
                )

            with (
                patch.object(generate_titles, "load_guideline", return_value="GUIDELINE"),
                patch.object(generate_titles, "load_top_titles", return_value="TOP_TITLES"),
                patch.object(generate_titles, "call_content_file_based", side_effect=fake_call),
            ):
                result = generate_titles.run_round1(
                    "CONTENT_SENTINEL",
                    brief,
                    round0,
                    "",
                    workspace,
                )

            self.assertEqual(len(prompts), 2)
            self.assertIn("CONTENT_SENTINEL", prompts[0])
            self.assertNotIn("BRIEF_SENTINEL", prompts[0])
            self.assertNotIn("ROUND0_SENTINEL", prompts[0])
            self.assertIn("INDEPENDENT_SENTINEL", prompts[1])
            self.assertIn("BRIEF_SENTINEL", prompts[1])
            self.assertIn("ROUND0_SENTINEL", prompts[1])
            self.assertEqual(result.name, "round1_review.md")
            self.assertTrue((workspace / "round1_independent.md").exists())

    def test_title_thumbnail_and_payoff_remain_one_delivery_contract(self) -> None:
        self.assertIn("标题 × 封面组合", generate_titles.ROUND0_PROMPT)
        self.assertIn("观众脑中被打开的那一个问题", generate_titles.ROUND0_PROMPT)
        self.assertIn("缩小后的封面", generate_titles.ROUND1_COMPARE_PROMPT)
        self.assertIn("## 首选组合", generate_titles.ROUND2_PROMPT)
        self.assertIn("**观众会追问：**", generate_titles.ROUND2_PROMPT)
        self.assertIn("视频兑现", generate_titles.ROUND2_PROMPT)

    def test_brief_uses_full_source_and_current_channel_context(self) -> None:
        self.assertIn("{content}", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("{guideline}", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertIn("科技、进步、AI 与个人成长", generate_titles.TITLE_BRIEF_PROMPT)
        self.assertNotIn("{round0}", generate_titles.TITLE_BRIEF_PROMPT)

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

    def test_article_with_srt_supplies_original_evidence_to_brief_and_challenger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "episode.article.md"
            article.write_text("ARTICLE_INTERPRETATION", encoding="utf-8")
            source_srt = root / "episode.final.srt"
            source_srt.write_text(
                "1\n00:00:00,000 --> 00:00:10,000\n"
                + ("前" * 6500)
                + "\n\n2\n00:12:00,000 --> 00:12:05,000\nORIGINAL_TAIL_EVIDENCE\n",
                encoding="utf-8",
            )
            prompts: list[str] = []

            def fake_call(prompt, output_path, model):
                prompts.append(prompt)
                output_path.write_text("DERIVED_OUTPUT", encoding="utf-8")

            with patch.object(generate_titles, "call_content_file_based", side_effect=fake_call):
                generate_titles.generate_titles(
                    article,
                    source_srt_path=source_srt,
                    stop_at_round=1,
                    discover_highlights=False,
                )

            self.assertEqual(len(prompts), 4)
            for prompt in (prompts[0], prompts[2]):
                self.assertIn("ORIGINAL_TAIL_EVIDENCE", prompt)
                self.assertIn("ARTICLE_INTERPRETATION", prompt)
                self.assertLess(
                    prompt.index("ORIGINAL_TAIL_EVIDENCE"),
                    prompt.index("ARTICLE_INTERPRETATION"),
                )

    def test_round2_receives_timed_source_and_current_guideline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brief = root / "brief.md"
            round0 = root / "round0.md"
            round1 = root / "round1.md"
            final_out = root / "candidate.md"
            brief.write_text("BRIEF", encoding="utf-8")
            round0.write_text("ROUND0", encoding="utf-8")
            round1.write_text("ROUND1", encoding="utf-8")
            captured: dict[str, str] = {}

            def fake_call(prompt, output_path, model):
                captured["prompt"] = prompt
                output_path.write_text("candidate", encoding="utf-8")

            with (
                patch.object(generate_titles, "call_content_file_based", side_effect=fake_call),
                patch.object(generate_titles, "load_guideline", return_value="CURRENT_GUIDELINE_SENTINEL"),
            ):
                generate_titles.run_round2(
                    brief,
                    round0,
                    round1,
                    "",
                    "[00:12:00,000 --> 00:12:01,000] TIMED_QUOTE_SENTINEL",
                    False,
                    final_out,
                )

            self.assertIn("TIMED_QUOTE_SENTINEL", captured["prompt"])
            self.assertIn("CURRENT_GUIDELINE_SENTINEL", captured["prompt"])

    def test_round2_does_not_invent_clip_timing_without_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brief = root / "brief.md"
            round0 = root / "round0.md"
            round1 = root / "round1.md"
            final_out = root / "candidate.md"
            for path in (brief, round0, round1):
                path.write_text(path.stem, encoding="utf-8")
            captured: dict[str, str] = {}

            def fake_call(prompt, output_path, model):
                captured["prompt"] = prompt
                output_path.write_text("candidate", encoding="utf-8")

            with patch.object(
                generate_titles,
                "call_content_file_based",
                side_effect=fake_call,
            ):
                generate_titles.run_round2(
                    brief,
                    round0,
                    round1,
                    "",
                    "",
                    False,
                    final_out,
                )

            self.assertIn("不得伪造时间点", captured["prompt"])
            self.assertIn("host-narrative", captured["prompt"])

    def test_round2_marks_validated_speaker_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [root / name for name in ("brief.md", "round0.md", "round1.md")]
            for path in files:
                path.write_text(path.stem, encoding="utf-8")
            captured: dict[str, str] = {}

            def fake_call(prompt, output_path, model):
                captured["prompt"] = prompt
                output_path.write_text("candidate", encoding="utf-8")

            with patch.object(generate_titles, "call_content_file_based", side_effect=fake_call):
                generate_titles.run_round2(
                    files[0],
                    files[1],
                    files[2],
                    "",
                    "[00:00:00,000 --> 00:00:01,000] HOST: hello",
                    True,
                    root / "candidate.md",
                )

            self.assertIn("已有经过当前 SRT 校验的 speaker label", captured["prompt"])

    def test_cue_timed_source_preserves_exact_in_and_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "episode.srt"
            srt.write_text(
                "1\n00:00:01,250 --> 00:00:03,750\n精确原话\n",
                encoding="utf-8",
            )

            text = generate_titles.cue_timed_text_from_srt(srt)

            self.assertEqual(
                text,
                "[00:00:01,250 --> 00:00:03,750] 精确原话",
            )

    def test_failed_opening_qc_gets_one_repair_before_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "episode.final.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n真实原话\n",
                encoding="utf-8",
            )

            def artifact(name: str, body: str):
                def create(*args, **kwargs):
                    path = root / name
                    path.write_text(body, encoding="utf-8")
                    return path

                return create

            def package(opening_quote: str) -> str:
                return (
                    "## 首选组合\n"
                    "**标题：** 标题\n"
                    "**封面主文案：** 大字\n"
                    "**封面画面：** 画面\n"
                    "**观众会追问：** 为什么\n"
                    "**视频兑现：** 00:00\n"
                    "**开头衔接：**\n"
                    "**开头类型：** source-cold-open\n"
                    f"- **原片：** 00:00:00,000 --> 00:00:02,000｜{opening_quote}\n"
                    "**进入正片：** 00:00:00,000\n"
                    "## 备选组合\n备选\n"
                    "## 放弃的方向\n放弃\n"
                )

            def invalid_round2(*args, **kwargs):
                output = args[-1]
                output.write_text(package("错误引语"), encoding="utf-8")
                return output

            repair_calls: list[list[str]] = []

            def valid_repair(candidate, errors, opening_source, verified, repaired_out):
                repair_calls.append(errors)
                repaired_out.write_text(package("真实原话"), encoding="utf-8")
                return repaired_out

            with (
                patch.object(generate_titles, "build_title_brief", side_effect=artifact("brief.md", "brief")),
                patch.object(generate_titles, "run_round0", side_effect=artifact("round0.md", "round0")),
                patch.object(generate_titles, "run_round1", side_effect=artifact("round1.md", "round1")),
                patch.object(generate_titles, "run_round2", side_effect=invalid_round2),
                patch.object(generate_titles, "repair_round2_opening", side_effect=valid_repair),
            ):
                result = generate_titles.generate_titles(
                    srt,
                    output_dir=root,
                    workspace_dir=root / "work",
                    discover_highlights=False,
                )

            self.assertEqual(len(repair_calls), 1)
            self.assertIn("真实原话", result.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
