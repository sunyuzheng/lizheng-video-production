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

    def test_gate_detects_overlap_short_and_fast(self):
        bad = [
            {"index": 1, "start": 0.0, "end": 0.1, "text": "这是一条过快字幕"},
            {"index": 2, "start": 0.05, "end": 1.0, "text": "下一条"},
        ]
        findings = subtitle_qc.inspect(bad)
        self.assertTrue(findings["overlaps"])
        self.assertTrue(findings["short"])
        self.assertTrue(findings["fast"])

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
