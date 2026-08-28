import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.process_video import transcribe


class TranscribeOutputSafetyTests(unittest.TestCase):
    def test_existing_qwen_is_replaced_only_by_a_new_current_run_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.write_bytes(b"new video")
            qwen = root / "episode.qwen.srt"
            qwen.write_text("old transcript", encoding="utf-8")

            def create_current_output(command, **_kwargs):
                run_dir = Path(command[command.index("--output-dir") + 1])
                (run_dir / "episode.srt").write_text(
                    "1\n00:00:00,000 --> 00:00:01,000\nnew transcript\n",
                    encoding="utf-8",
                )

            with (
                patch("tools.process_video._resolve_asr_cli", return_value="mlx-qwen3-asr"),
                patch(
                    "tools.process_video.subprocess.run",
                    side_effect=create_current_output,
                ) as runner,
            ):
                result = transcribe(video, output_dir=root)

            runner.assert_called_once()
            self.assertEqual(result, qwen)
            self.assertIn("new transcript", qwen.read_text(encoding="utf-8"))
            self.assertNotIn("old transcript", qwen.read_text(encoding="utf-8"))

    def test_failed_rerun_preserves_existing_qwen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.write_bytes(b"video")
            qwen = root / "episode.qwen.srt"
            qwen.write_text("known-good", encoding="utf-8")

            with (
                patch("tools.process_video._resolve_asr_cli", return_value="mlx-qwen3-asr"),
                patch("tools.process_video.subprocess.run", return_value=None),
            ):
                with self.assertRaises(SystemExit):
                    transcribe(video, output_dir=root)

            self.assertEqual(qwen.read_text(encoding="utf-8"), "known-good")

    def test_unrelated_shared_srt_is_never_adopted_or_moved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.write_bytes(b"video")
            unrelated = root / "another.corrected.srt"
            unrelated.write_text("known unrelated content", encoding="utf-8")

            def modify_unrelated(_command, **_kwargs):
                unrelated.write_text("externally updated", encoding="utf-8")

            with (
                patch("tools.process_video._resolve_asr_cli", return_value="mlx-qwen3-asr"),
                patch(
                    "tools.process_video.subprocess.run",
                    side_effect=modify_unrelated,
                ),
            ):
                with self.assertRaises(SystemExit):
                    transcribe(video, output_dir=root)

            self.assertTrue(unrelated.exists())
            self.assertEqual(
                unrelated.read_text(encoding="utf-8"), "externally updated"
            )
            self.assertFalse((root / "episode.qwen.srt").exists())

    def test_exit_zero_without_new_output_never_accepts_stale_generic_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "episode.mp4"
            video.write_bytes(b"video")
            stale = root / "episode.srt"
            stale.write_text("stale", encoding="utf-8")

            with (
                patch("tools.process_video._resolve_asr_cli", return_value="mlx-qwen3-asr"),
                patch("tools.process_video.subprocess.run", return_value=None),
            ):
                with self.assertRaises(SystemExit):
                    transcribe(video, output_dir=root)

            self.assertEqual(stale.read_text(encoding="utf-8"), "stale")
            self.assertFalse((root / "episode.qwen.srt").exists())


if __name__ == "__main__":
    unittest.main()
