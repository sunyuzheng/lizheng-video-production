import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

from tools.render_filler_cuts import (
    Cut,
    default_clean_srt_candidate,
    load_cuts,
    render,
    render_with_subtitle_bundle,
    retime_srt,
    validate_artifact_paths,
    validate_cuts,
)


class FillerCutPlanTests(unittest.TestCase):
    def test_default_retimed_subtitle_is_never_named_final(self) -> None:
        output = Path("/tmp/video.clean.mp4")
        self.assertEqual(
            default_clean_srt_candidate(output),
            Path("/tmp/video.clean.candidate.srt"),
        )

    def test_only_explicit_cut_decisions_are_applied(self) -> None:
        payload = {
            "cuts": [
                {"start": 1, "end": 2, "label": "missing decision"},
                {"start": 3, "end": 4, "decision": "keep"},
                {"start": 5, "end": 6, "decision": "cut", "label": "呃"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"
            plan.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            cuts = load_cuts(plan)
        self.assertEqual([(cut.start, cut.end) for cut in cuts], [(5.0, 6.0)])

    def test_overlapping_reviewed_cuts_fail_closed(self) -> None:
        payload = {
            "cuts": [
                {"start": 1, "end": 2, "decision": "cut"},
                {"start": 1.5, "end": 3, "decision": "cut"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"
            plan.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlapping reviewed cuts"):
                load_cuts(plan)

    def test_cut_bounds_are_checked_against_media(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds media duration"):
            validate_cuts([Cut(9, 11, "", "")], duration=10)
        with self.assertRaisesRegex(ValueError, "entire video"):
            validate_cuts([Cut(0, 10, "", "")], duration=10)

    def test_srt_is_retimed_and_fully_removed_cue_is_dropped(self) -> None:
        source_text = """1
00:00:00,000 --> 00:00:01,000
保留

2
00:00:01,000 --> 00:00:02,000
删除

3
00:00:02,000 --> 00:00:03,000
后一句
"""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.srt"
            output = Path(tmp) / "clean.srt"
            source.write_text(source_text, encoding="utf-8")
            retime_srt(source, output, [Cut(1, 2, "", "")])
            result = output.read_text(encoding="utf-8")
        self.assertNotIn("删除", result)
        self.assertIn("00:00:01,000 --> 00:00:02,000\n后一句", result)

    def test_retiming_never_overwrites_source_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.srt"
            source.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n保留\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                retime_srt(source, source, [Cut(0.1, 0.2, "", "")])
            self.assertIn("保留", source.read_text(encoding="utf-8"))

    def test_video_plan_and_subtitle_roles_must_be_distinct(self) -> None:
        same = Path("/tmp/source.mp4")
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            validate_artifact_paths(
                {"source_video": same, "output_video": same}
            )

    def test_failed_render_preserves_existing_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            output = root / "clean.mp4"
            source.write_bytes(b"source")
            output.write_bytes(b"known-good")

            def fake_audio(_video, _cuts, workdir, _duration):
                audio = workdir / "clean.wav"
                audio.write_bytes(b"audio")
                return audio

            def fail_after_partial(command, **_kwargs):
                Path(command[-1]).write_bytes(b"partial")
                raise subprocess.CalledProcessError(1, command)

            with (
                patch("tools.render_filler_cuts.probe_duration", return_value=10.0),
                patch("tools.render_filler_cuts.build_clean_audio", side_effect=fake_audio),
                patch("tools.render_filler_cuts.subprocess.run", side_effect=fail_after_partial),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    render(source, output, [Cut(1, 2, "", "")], "1M")

            self.assertEqual(output.read_bytes(), b"known-good")
            self.assertFalse((root / ".clean.rendering.mp4").exists())

    def test_malformed_srt_preserves_existing_video_and_subtitle_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_video = root / "source.mp4"
            output_video = root / "clean.mp4"
            source_srt = root / "source.srt"
            output_srt = root / "clean.candidate.srt"
            source_video.write_bytes(b"source")
            output_video.write_bytes(b"known-good-video")
            source_srt.write_text("BROKEN", encoding="utf-8")
            output_srt.write_text("known-good-srt", encoding="utf-8")

            with patch("tools.render_filler_cuts.render") as render_mock:
                with self.assertRaisesRegex(ValueError, "无法完整解析"):
                    render_with_subtitle_bundle(
                        source_video,
                        output_video,
                        [Cut(0.1, 0.2, "", "")],
                        "1M",
                        source_srt,
                        output_srt,
                    )

            render_mock.assert_not_called()
            self.assertEqual(output_video.read_bytes(), b"known-good-video")
            self.assertEqual(
                output_srt.read_text(encoding="utf-8"), "known-good-srt"
            )


if __name__ == "__main__":
    unittest.main()
