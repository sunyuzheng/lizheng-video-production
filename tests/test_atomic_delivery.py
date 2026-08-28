import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import atomic_delivery


class AtomicDeliveryTests(unittest.TestCase):
    def test_second_failure_restores_entire_old_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_new = root / ".first.new"
            second_new = root / ".second.new"
            first = root / "first.txt"
            second = root / "second.txt"
            first_new.write_text("new first", encoding="utf-8")
            second_new.write_text("new second", encoding="utf-8")
            first.write_text("old first", encoding="utf-8")
            second.write_text("old second", encoding="utf-8")
            real_replace = atomic_delivery._replace_path

            def fail_second(source, destination):
                if source == second_new.resolve():
                    raise OSError("simulated second failure")
                return real_replace(source, destination)

            with patch.object(
                atomic_delivery, "_replace_path", side_effect=fail_second
            ):
                with self.assertRaisesRegex(OSError, "second failure"):
                    atomic_delivery.commit_prepared_files(
                        [(first_new, first), (second_new, second)]
                    )

            self.assertEqual(first.read_text(encoding="utf-8"), "old first")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second")

    def test_success_commits_entire_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one_new = root / ".one.new"
            two_new = root / ".two.new"
            one = root / "one.txt"
            two = root / "two.txt"
            one_new.write_text("one", encoding="utf-8")
            two_new.write_text("two", encoding="utf-8")

            atomic_delivery.commit_prepared_files(
                [(one_new, one), (two_new, two)]
            )

            self.assertEqual(one.read_text(encoding="utf-8"), "one")
            self.assertEqual(two.read_text(encoding="utf-8"), "two")


if __name__ == "__main__":
    unittest.main()
