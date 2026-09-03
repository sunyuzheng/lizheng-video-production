import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import process_video


class ProcessVideoFailFastTests(unittest.TestCase):
    def test_explicit_final_reuse_preserves_text_and_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.touch()
            final_srt = root / "episode.final.srt"
            original = "1\n00:00:00,000 --> 00:00:02,000\n这是现有的终稿字幕。\n"
            final_srt.write_text(original, encoding="utf-8")
            process_dir = root / "work"

            argv = [
                "process_video.py",
                str(video),
                "--process-dir",
                str(process_dir),
                "--subtitle-source",
                str(final_srt),
                "--skip-highlights",
                "--skip-article",
                "--skip-titles",
                "--skip-youtube-description",
                "--no-seeds",
            ]
            output = io.StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(output):
                process_video.main()

            self.assertEqual(final_srt.read_text(encoding="utf-8"), original)
            self.assertTrue((root / "episode.final.vtt").exists())
            self.assertIn(
                "↻ episode.final.srt — 显式复用（本次 QC 通过）",
                output.getvalue(),
            )

    def test_fresh_asr_plus_skip_correct_never_reuses_old_corrected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.touch()
            process_dir = root / "work"
            process_dir.mkdir()
            old_corrected = process_dir / "episode.corrected.srt"
            old_corrected.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nOLD\n", encoding="utf-8"
            )
            fresh_qwen = process_dir / "episode.qwen.srt"
            captured: dict[str, Path] = {}

            def fake_transcribe(_video, output_dir, context=""):
                fresh_qwen.write_text(
                    "1\n00:00:00,000 --> 00:00:01,000\nFRESH\n",
                    encoding="utf-8",
                )
                return fresh_qwen

            def fake_resplit(source, output_path, max_chars=20, diagnostics=None):
                captured["source"] = source
                output_path.write_text(fresh_qwen.read_text(encoding="utf-8"), encoding="utf-8")
                if diagnostics is not None:
                    diagnostics.update({"risk": False})
                return output_path

            def fake_validate(
                candidate, final_srt, final_vtt, report, max_chars=20, **_kwargs
            ):
                final_srt.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
                final_vtt.write_text("WEBVTT\n", encoding="utf-8")
                return True

            argv = [
                "process_video.py",
                str(video),
                "--process-dir",
                str(process_dir),
                "--skip-correct",
                "--skip-highlights",
                "--skip-article",
                "--skip-titles",
                "--skip-youtube-description",
                "--no-seeds",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(process_video, "transcribe", side_effect=fake_transcribe),
                patch.object(process_video, "resplit", side_effect=fake_resplit),
                patch.object(
                    process_video,
                    "validate_and_export_subtitles",
                    side_effect=fake_validate,
                ),
            ):
                process_video.main()

            self.assertEqual(captured["source"], fresh_qwen)
            self.assertNotEqual(captured["source"], old_corrected)

    def test_skipped_content_steps_do_not_inject_stale_article_or_highlights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.touch()
            process_dir = root / "work"
            process_dir.mkdir()
            qwen = process_dir / "episode.qwen.srt"
            qwen.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nCURRENT\n",
                encoding="utf-8",
            )
            (root / "episode.article.md").write_text("STALE ARTICLE", encoding="utf-8")
            (root / "episode.highlights.md").write_text("STALE HIGHLIGHT", encoding="utf-8")
            captured: dict = {}

            def fake_resplit(source, output_path, max_chars=20, diagnostics=None):
                output_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                if diagnostics is not None:
                    diagnostics.update({"risk": False})
                return output_path

            def fake_validate(
                candidate, final_srt, final_vtt, report, max_chars=20, **_kwargs
            ):
                final_srt.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
                final_vtt.write_text("WEBVTT\n", encoding="utf-8")
                return True

            def fake_titles(content_path, **kwargs):
                captured["content_path"] = content_path
                captured.update(kwargs)
                output = root / "episode.titles.md"
                output.write_text("CURRENT TITLES", encoding="utf-8")
                return output

            argv = [
                "process_video.py",
                str(video),
                "--process-dir",
                str(process_dir),
                "--skip-transcribe",
                "--skip-correct",
                "--skip-highlights",
                "--skip-article",
                "--skip-youtube-description",
                "--no-seeds",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(process_video, "resplit", side_effect=fake_resplit),
                patch.object(
                    process_video,
                    "validate_and_export_subtitles",
                    side_effect=fake_validate,
                ),
                patch.object(process_video, "titles", side_effect=fake_titles),
            ):
                process_video.main()

            self.assertEqual(
                captured["content_path"], (root / "episode.final.srt").resolve()
            )
            self.assertIsNone(captured["highlights_path"])
            self.assertFalse(captured["discover_highlights"])

    def test_legacy_qwen_is_staged_without_modifying_delivery_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "delivery" / "episode.qwen.srt"
            staged = root / "work" / "episode.qwen.srt"
            legacy.parent.mkdir()
            legacy.write_text("legacy raw", encoding="utf-8")

            result = process_video.stage_legacy_qwen(legacy, staged)

            self.assertEqual(result, staged)
            self.assertEqual(staged.read_text(encoding="utf-8"), "legacy raw")
            self.assertEqual(legacy.read_text(encoding="utf-8"), "legacy raw")

    def test_malformed_source_in_default_correction_path_writes_qc_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.touch()
            process_dir = root / "work"
            process_dir.mkdir()
            (process_dir / "episode.qwen.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n测试\n\nBROKEN",
                encoding="utf-8",
            )
            argv = [
                "process_video.py",
                str(video),
                "--process-dir",
                str(process_dir),
                "--skip-transcribe",
                "--no-seeds",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    process_video,
                    "correct",
                    side_effect=ValueError("SRT 第 2 块无法完整解析"),
                ),
            ):
                with self.assertRaises(SystemExit) as exit_info:
                    process_video.main()

            self.assertEqual(exit_info.exception.code, 1)
            report = process_dir / "episode.subtitle_qc.md"
            self.assertTrue(report.exists())
            self.assertIn("未通过", report.read_text(encoding="utf-8"))

    def test_malformed_source_writes_qc_report_before_stopping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.touch()
            process_dir = root / "work"
            process_dir.mkdir()
            (process_dir / "episode.qwen.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n测试\n\nBROKEN",
                encoding="utf-8",
            )
            argv = [
                "process_video.py",
                str(video),
                "--process-dir",
                str(process_dir),
                "--skip-transcribe",
                "--skip-correct",
                "--no-seeds",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(process_video, "highlights") as highlights,
            ):
                with self.assertRaises(SystemExit) as exit_info:
                    process_video.main()

            self.assertEqual(exit_info.exception.code, 1)
            report = process_dir / "episode.subtitle_qc.md"
            self.assertTrue(report.exists())
            self.assertIn("未通过", report.read_text(encoding="utf-8"))
            highlights.assert_not_called()

    def test_subtitle_qc_failure_prevents_all_content_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.touch()
            process_dir = root / "work"
            process_dir.mkdir()
            qwen = process_dir / "episode.qwen.srt"
            qwen.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8"
            )

            def fake_resplit(_source, output_path, max_chars=20, diagnostics=None):
                output_path.write_text(qwen.read_text(encoding="utf-8"), encoding="utf-8")
                if diagnostics is not None:
                    diagnostics.update({"risk": False})
                return output_path

            argv = [
                "process_video.py",
                str(video),
                "--process-dir",
                str(process_dir),
                "--skip-transcribe",
                "--skip-correct",
                "--no-seeds",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(process_video, "resplit", side_effect=fake_resplit),
                patch.object(
                    process_video, "validate_and_export_subtitles", return_value=False
                ),
                patch.object(process_video, "highlights") as highlights,
                patch.object(process_video, "article") as article,
                patch.object(process_video, "titles") as titles,
                patch.object(process_video, "youtube_description") as description,
            ):
                with self.assertRaises(SystemExit) as exit_info:
                    process_video.main()

            self.assertEqual(exit_info.exception.code, 1)
            highlights.assert_not_called()
            article.assert_not_called()
            titles.assert_not_called()
            description.assert_not_called()


if __name__ == "__main__":
    unittest.main()
