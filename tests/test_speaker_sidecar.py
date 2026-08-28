import tempfile
import unittest
from pathlib import Path

from tools.speaker_sidecar import load_validated_speaker_srt


SOURCE = """1
00:00:00,000 --> 00:00:01,000
第一句

2
00:00:01,000 --> 00:00:02,000
第二句
"""


class SpeakerSidecarTests(unittest.TestCase):
    def test_matching_labeled_srt_is_accepted(self) -> None:
        labeled = SOURCE.replace("第一句", "主持人：第一句").replace(
            "第二句", "嘉宾：第二句"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.final.srt"
            sidecar = root / "episode.speaker_labeled.srt"
            source.write_text(SOURCE, encoding="utf-8")
            sidecar.write_text(labeled, encoding="utf-8")

            result = load_validated_speaker_srt(source, [sidecar])

            self.assertIn("主持人：第一句", result)

    def test_stale_words_or_timeline_are_rejected(self) -> None:
        stale = SOURCE.replace("第一句", "主持人：旧的第一句")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.final.srt"
            sidecar = root / "episode.speaker_labeled.srt"
            source.write_text(SOURCE, encoding="utf-8")
            sidecar.write_text(stale, encoding="utf-8")

            self.assertEqual(load_validated_speaker_srt(source, [sidecar]), "")

    def test_unverifiable_markdown_never_replaces_current_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "episode.article.md"
            sidecar = root / "episode.speaker_labeled.srt"
            article.write_text("CURRENT", encoding="utf-8")
            sidecar.write_text(SOURCE, encoding="utf-8")

            self.assertEqual(load_validated_speaker_srt(article, [sidecar]), "")


if __name__ == "__main__":
    unittest.main()
