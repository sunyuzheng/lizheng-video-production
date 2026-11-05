import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills' / 'video-title-and-cover' / 'scripts' / 'feed_preview.py'

try:
    from PIL import Image
except ImportError:  # the implementation venv does not ship Pillow; system python3 usually has it
    Image = None


def load_script():
    spec = importlib.util.spec_from_file_location('feed_preview', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(Image is None, 'Pillow is not installed')
class FeedPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fp = load_script()

    def test_classifies_covers_by_aspect_ratio(self) -> None:
        self.assertEqual(self.fp.classify(Image.new('RGB', (1280, 720))), '169')
        self.assertEqual(self.fp.classify(Image.new('RGB', (2560, 1440))), '169')
        self.assertEqual(self.fp.classify(Image.new('RGB', (1080, 1440))), '34')
        self.assertIsNone(self.fp.classify(Image.new('RGB', (1080, 1920))))

    def test_line_heights_accept_percent_or_fraction(self) -> None:
        self.assertEqual(self.fp.percents('16,24'), [0.16, 0.24])
        self.assertEqual(self.fp.percents('0.16, 6.5'), [0.16, 0.065])

    def test_writes_one_sheet_and_skips_other_ratios(self) -> None:
        try:
            self.fp.Fonts()
        except SystemExit:
            self.skipTest('no Chinese font on this machine')
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            Image.new('RGB', (1280, 720), 'navy').save(tmp / 'wide.png')
            Image.new('RGB', (1080, 1440), 'teal').save(tmp / 'tall.png')
            Image.new('RGB', (1080, 1920), 'gray').save(tmp / 'story.png')
            report, warnings = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(report), contextlib.redirect_stderr(warnings):
                out = self.fp.main([f"旧版={tmp / 'wide.png'}", str(tmp / 'tall.png'), str(tmp / 'story.png'),
                                    '--grid3', '--ink', '16,24', '-o', str(tmp / 'sheet.png')])
            with Image.open(out) as sheet:
                self.assertGreater(sheet.height, 1000)
            self.assertIn('story.png', warnings.getvalue())
            self.assertIn('YouTube 推荐栏 168×94', report.getvalue())
            self.assertIn('小红书双列卡片', report.getvalue())

    def test_refuses_when_no_cover_fits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'story.png'
            Image.new('RGB', (1080, 1920)).save(path)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.fp.main([str(path), '-o', str(Path(tmp) / 'sheet.png')])


if __name__ == '__main__':
    unittest.main()
