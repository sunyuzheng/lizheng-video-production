#!/usr/bin/env python3

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import resplit_srt  # noqa: E402
import subtitle_qc  # noqa: E402
import process_video  # noqa: E402
from tools import atomic_delivery  # noqa: E402


class TestNaturalResplit(unittest.TestCase):
    def test_resplit_normalization_never_guesses_product_or_removes_repetition(self):
        text = "CloudCode 和 DeepSeekDeepSeek 都出现在原话里"
        normalized = resplit_srt.normalize_text(text)
        self.assertIn("CloudCode", normalized)
        self.assertIn("DeepSeekDeepSeek", normalized)
        self.assertNotIn("Claude Code", normalized)

    def test_latin_word_is_never_split(self):
        text = "这是一个很长的中文铺垫用来测试 desperation 之后继续说"
        segments = resplit_srt.split_text(text, max_chars=20)
        self.assertEqual(sum("desperation" in s for s in segments), 1)
        self.assertFalse(any(s.endswith("despera") or s.startswith("tion") for s in segments))

    def test_fast_cue_borrows_local_slack(self):
        result = [
            {"timestamp": "00:00:00,000 --> 00:00:01,000", "text": "前一句"},
            {"timestamp": "00:00:01,000 --> 00:00:01,400", "text": "desperation"},
            {"timestamp": "00:00:01,400 --> 00:00:02,000", "text": "后一句"},
        ]
        repaired = resplit_srt._repair_display_timing(result)
        start, end = resplit_srt._parse_ts(repaired[1]["timestamp"])
        self.assertGreaterEqual(end - start, 11 / 25 - 0.001)
        self.assertEqual(resplit_srt._parse_ts(repaired[0]["timestamp"])[0], 0.0)
        self.assertEqual(resplit_srt._parse_ts(repaired[-1]["timestamp"])[1], 2.0)

    def test_default_output_is_candidate_not_delivery(self):
        source = """1
00:00:00,000 --> 00:00:01,000
第一句
"""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "episode.corrected.srt"
            input_path.write_text(source, encoding="utf-8")

            output_path = resplit_srt.resplit_srt(input_path)

            self.assertEqual(output_path.name, "episode.final.candidate.srt")
            self.assertFalse((Path(tmp) / "episode.final.srt").exists())

    def test_explicit_output_never_overwrites_input(self):
        source = "1\n00:00:00,000 --> 00:00:01,000\n第一句\n"
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "episode.corrected.srt"
            input_path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不能覆盖"):
                resplit_srt.resplit_srt(input_path, output_path=input_path)
            self.assertEqual(input_path.read_text(encoding="utf-8"), source)

    def test_long_unpunctuated_source_records_fallback_saturation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.corrected.srt"
            source.write_text(
                "".join(
                    f"{i + 1}\n00:00:{i * 5:02d},000 --> "
                    f"00:00:{i * 5 + 5:02d},000\n{'字' * 50}\n\n"
                    for i in range(10)
                ),
                encoding="utf-8",
            )
            diagnostics: dict = {}

            resplit_srt.resplit_srt(source, max_chars=20, diagnostics=diagnostics)

            self.assertTrue(diagnostics["requires_semantic_review"])
            self.assertIn("LOW_PUNCTUATION", diagnostics["reason_codes"])
            self.assertIn(
                "FALLBACK_BOUNDARY_SATURATION", diagnostics["reason_codes"]
            )
            self.assertIn("NEAR_LIMIT_FALLBACK_RUN", diagnostics["reason_codes"])
            self.assertGreaterEqual(
                diagnostics["longest_near_limit_fallback_run"], 6
            )
            self.assertGreater(
                diagnostics["boundary_source_counts"].get("merge_size_cap", 0), 0
            )

    def test_normal_punctuation_does_not_trigger_semantic_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "episode.corrected.srt"
            sentence = "这是一个意思完整而且标点清楚的句子。"
            source.write_text(
                f"1\n00:00:00,000 --> 00:01:00,000\n{sentence * 40}\n",
                encoding="utf-8",
            )
            diagnostics: dict = {}

            resplit_srt.resplit_srt(source, max_chars=20, diagnostics=diagnostics)

            self.assertFalse(diagnostics["requires_semantic_review"])
            self.assertNotIn("LOW_PUNCTUATION", diagnostics["reason_codes"])

    def test_local_bad_region_is_not_diluted_by_normal_punctuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "episode.corrected.srt"
            normal = "这是一个意思完整的短句。" * 80
            bad_region = "字" * 220
            source.write_text(
                f"1\n00:00:00,000 --> 00:02:00,000\n"
                f"{normal}{bad_region}{normal}\n",
                encoding="utf-8",
            )
            diagnostics: dict = {}

            resplit_srt.resplit_srt(source, max_chars=20, diagnostics=diagnostics)

            self.assertGreater(diagnostics["breaks_per_100"], 0.5)
            self.assertTrue(diagnostics["requires_semantic_review"])
            self.assertIn("NEAR_LIMIT_FALLBACK_RUN", diagnostics["reason_codes"])

    def test_dispersed_short_fallback_runs_do_not_fail_on_global_average(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "episode.corrected.srt"
            source.write_text(
                f"1\n00:00:00,000 --> 00:02:00,000\n{('字' * 80 + '。') * 20}\n",
                encoding="utf-8",
            )
            diagnostics: dict = {}

            resplit_srt.resplit_srt(source, max_chars=20, diagnostics=diagnostics)

            self.assertIn(
                "FALLBACK_BOUNDARY_SATURATION", diagnostics["reason_codes"]
            )
            self.assertLess(diagnostics["longest_near_limit_fallback_run"], 6)
            self.assertFalse(diagnostics["requires_semantic_review"])


class TestSubtitleQc(unittest.TestCase):
    VALID = """1
00:00:00,000 --> 00:00:01,000
第一句

2
00:00:01,000 --> 00:00:02,000
desperation
"""

    def test_valid_srt_exports_matching_vtt(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "valid.srt"
            vtt = Path(tmp) / "valid.vtt"
            srt.write_text(self.VALID, encoding="utf-8")
            cues = subtitle_qc.parse_srt(srt)
            findings = subtitle_qc.inspect(cues)
            self.assertEqual(sum(len(items) for items in findings.values()), 0)
            subtitle_qc.write_vtt(cues, vtt)
            text = vtt.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("WEBVTT\n"))
            self.assertIn("desperation", text)

    def test_report_records_the_thresholds_used_for_qc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "valid.srt"
            report = root / "report.md"
            srt.write_text(self.VALID, encoding="utf-8")
            cues = subtitle_qc.parse_srt(srt)
            findings = subtitle_qc.inspect(
                cues, max_chars=27, min_duration=0.25, max_cps=22.0
            )
            subtitle_qc.write_report(
                cues,
                findings,
                report,
                max_chars=27,
                min_duration=0.25,
                max_cps=22.0,
            )

            text = report.read_text(encoding="utf-8")
            self.assertIn("可见字符上限：27", text)
            self.assertIn("最短显示时长：0.25 秒", text)
            self.assertIn("阅读速度上限：22 字符／秒", text)

    def test_gate_detects_overlap_short_and_fast(self):
        bad = [
            {"index": 1, "start": 0.0, "end": 0.1, "text": "这是一条过快字幕"},
            {"index": 2, "start": 0.05, "end": 1.0, "text": "下一条"},
        ]
        findings = subtitle_qc.inspect(bad)
        self.assertTrue(findings["overlaps"])
        self.assertTrue(findings["short"])
        self.assertTrue(findings["fast"])

    def test_boundary_screen_blocks_machine_packed_cues(self):
        cues = [
            {"index": i, "text": "字" * 20}
            for i in range(1, 25)
        ]
        result = subtitle_qc.inspect_boundary_quality(cues, max_chars=20)

        self.assertTrue(result["risk"])
        self.assertEqual(result["packed_without_boundary_count"], 24)
        self.assertEqual(result["longest_packed_run"], 24)

    def test_boundary_screen_does_not_claim_semantic_failure_for_varied_cues(self):
        cues = [
            {
                "index": i,
                "text": "这是一个完整的意思。" if i % 2 else "下一句也自然停在这里，",
            }
            for i in range(1, 25)
        ]
        result = subtitle_qc.inspect_boundary_quality(cues, max_chars=20)

        self.assertFalse(result["risk"])

    def test_candidate_shape_screen_is_not_skipped_when_provenance_exists(self):
        cues = [{"index": i, "text": "字" * 20} for i in range(1, 25)]
        provenance = {
            "method": "split_provenance",
            "risk": False,
            "reason_codes": [],
        }
        candidate_shape = subtitle_qc.inspect_boundary_quality(cues, max_chars=20)

        merged = subtitle_qc.merge_boundary_quality(provenance, candidate_shape)

        self.assertTrue(merged["risk"])
        self.assertIn("NEAR_LIMIT_BOUNDARY_SATURATION", merged["reason_codes"])

    def test_lexical_stream_allows_resegmentation_and_punctuation_only(self):
        source = [{"text": "洪水需要船文艺复兴需要城"}]
        candidate = [{"text": "洪水需要船，"}, {"text": "文艺复兴需要城。"}]
        changed = [{"text": "洪水需要船，"}, {"text": "文艺复兴需要。"}]

        self.assertTrue(
            subtitle_qc.compare_lexical_streams(source, candidate)["matches"]
        )
        self.assertFalse(
            subtitle_qc.compare_lexical_streams(source, changed)["matches"]
        )

    def test_parser_never_silently_ignores_malformed_block(self):
        malformed = self.VALID + "\nTHIS BLOCK IS NOT SRT\n"
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "malformed.srt"
            output = Path(tmp) / "final.srt"
            srt.write_text(malformed, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不会静默忽略"):
                subtitle_qc.parse_srt(srt)
            with self.assertRaisesRegex(ValueError, "不会静默忽略"):
                resplit_srt.resplit_srt(srt, output_path=output)
            self.assertFalse(output.exists())

    def test_failed_qc_writes_report_without_promoting_delivery(self):
        bad = """1
00:00:00,000 --> 00:00:00,100
这是一条过快字幕
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.srt"
            final_srt = root / "episode.final.srt"
            final_vtt = root / "episode.final.vtt"
            report = root / "episode.subtitle_qc.md"
            candidate.write_text(bad, encoding="utf-8")
            final_srt.write_text("previous srt", encoding="utf-8")
            final_vtt.write_text("previous vtt", encoding="utf-8")

            passed = process_video.validate_and_export_subtitles(
                candidate, final_srt, final_vtt, report
            )

            self.assertFalse(passed)
            self.assertEqual(final_srt.read_text(encoding="utf-8"), "previous srt")
            self.assertEqual(final_vtt.read_text(encoding="utf-8"), "previous vtt")
            self.assertIn("未通过", report.read_text(encoding="utf-8"))

    def test_machine_packed_candidate_is_not_promoted(self):
        def stamp(seconds: int) -> str:
            return f"00:00:{seconds:02d},000"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.corrected.srt"
            candidate = root / "episode.final.candidate.srt"
            final_srt = root / "episode.final.srt"
            final_vtt = root / "episode.final.vtt"
            report = root / "episode.subtitle_qc.md"
            chunks = ["字" * 20 for _ in range(24)]
            source.write_text(
                f"1\n00:00:00,000 --> 00:00:48,000\n{''.join(chunks)}\n",
                encoding="utf-8",
            )
            candidate.write_text(
                "".join(
                    f"{i + 1}\n{stamp(i * 2)} --> {stamp(i * 2 + 2)}\n{text}\n\n"
                    for i, text in enumerate(chunks)
                ),
                encoding="utf-8",
            )
            final_srt.write_text("known-good srt", encoding="utf-8")
            final_vtt.write_text("known-good vtt", encoding="utf-8")

            passed = process_video.validate_and_export_subtitles(
                candidate,
                final_srt,
                final_vtt,
                report,
                max_chars=20,
                source_srt=source,
                check_boundary_quality=True,
            )

            self.assertFalse(passed)
            self.assertEqual(final_srt.read_text(encoding="utf-8"), "known-good srt")
            self.assertEqual(final_vtt.read_text(encoding="utf-8"), "known-good vtt")
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("正文字符流与精校源一致：是", report_text)
            self.assertIn("自动断句可信度：需语义复核", report_text)

    def test_passed_qc_promotes_exact_srt_and_vtt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.srt"
            final_srt = root / "episode.final.srt"
            final_vtt = root / "episode.final.vtt"
            report = root / "episode.subtitle_qc.md"
            candidate.write_text(self.VALID, encoding="utf-8")

            passed = process_video.validate_and_export_subtitles(
                candidate, final_srt, final_vtt, report
            )

            self.assertTrue(passed)
            self.assertEqual(final_srt.read_text(encoding="utf-8"), self.VALID)
            self.assertTrue(final_vtt.read_text(encoding="utf-8").startswith("WEBVTT\n"))
            self.assertIn("通过", report.read_text(encoding="utf-8"))

    def test_standalone_candidate_promotion_keeps_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "video.clean.candidate.srt"
            final = root / "video.clean.final.srt"
            candidate.write_text(self.VALID, encoding="utf-8")
            subtitle_qc.promote_srt(candidate, final)

            self.assertEqual(final.read_text(encoding="utf-8"), self.VALID)
            self.assertTrue(candidate.exists())

    def test_artifact_roles_must_not_alias(self):
        same = Path("/tmp/episode.srt")
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            subtitle_qc.validate_distinct_paths(
                {"input_srt": same, "final_vtt": same}
            )

    def test_pair_promotion_restores_old_delivery_if_second_commit_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "episode.final.candidate.srt"
            final_srt = root / "episode.final.srt"
            final_vtt = root / "episode.final.vtt"
            candidate.write_text(self.VALID, encoding="utf-8")
            final_srt.write_text("old srt", encoding="utf-8")
            final_vtt.write_text("old vtt", encoding="utf-8")
            cues = subtitle_qc.parse_srt(candidate)
            real_replace = atomic_delivery._replace_path

            def fail_vtt_commit(source, destination):
                if destination == final_vtt.resolve() and ".candidate." in source.name:
                    raise OSError("simulated second commit failure")
                return real_replace(source, destination)

            with patch.object(
                atomic_delivery, "_replace_path", side_effect=fail_vtt_commit
            ):
                with self.assertRaisesRegex(OSError, "second commit failure"):
                    subtitle_qc.promote_subtitle_pair(
                        candidate, cues, final_srt, final_vtt
                    )

            self.assertEqual(final_srt.read_text(encoding="utf-8"), "old srt")
            self.assertEqual(final_vtt.read_text(encoding="utf-8"), "old vtt")
            self.assertTrue(candidate.exists())

    def test_warn_only_failure_never_overwrites_existing_vtt(self):
        bad = "1\n00:00:00,000 --> 00:00:00,100\n这是一条过快字幕\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "bad.srt"
            vtt = root / "episode.final.vtt"
            srt.write_text(bad, encoding="utf-8")
            vtt.write_text("known-good vtt", encoding="utf-8")
            argv = [
                "subtitle_qc.py",
                str(srt),
                "--write-vtt",
                str(vtt),
                "--warn-only",
            ]
            with patch.object(sys, "argv", argv):
                subtitle_qc.main()

            self.assertEqual(vtt.read_text(encoding="utf-8"), "known-good vtt")


if __name__ == "__main__":
    unittest.main()
