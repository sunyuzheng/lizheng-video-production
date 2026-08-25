#!/usr/bin/env python3

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import resplit_srt  # noqa: E402
import subtitle_qc  # noqa: E402


class TestNaturalResplit(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
