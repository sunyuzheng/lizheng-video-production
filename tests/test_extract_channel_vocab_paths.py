import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import extract_channel_vocab


class ChannelRootResolutionTests(unittest.TestCase):
    def test_cli_value_precedes_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"KEDAIBIAO_CHANNEL_ROOT": "/ignored"}
        ):
            resolved = extract_channel_vocab.resolve_channel_root(tmp)
        self.assertEqual(resolved, Path(tmp).resolve())

    def test_environment_is_used_without_cli_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"KEDAIBIAO_CHANNEL_ROOT": tmp}
        ):
            resolved = extract_channel_vocab.resolve_channel_root()
        self.assertEqual(resolved, Path(tmp).resolve())

    def test_conventional_sibling_is_portable_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            resolved = extract_channel_vocab.resolve_channel_root()
        self.assertEqual(resolved, extract_channel_vocab._DEFAULT_CHANNEL.resolve())
        self.assertEqual(extract_channel_vocab._DEFAULT_CHANNEL.name, "kedaibiao-channel")

    def test_runtime_schema_excludes_unverified_audit_maps(self) -> None:
        verified = {
            "可信词": {
                "alternatives": ["正确词"],
                "hint": "人工确认",
                "count": 42,
            }
        }
        runtime = extract_channel_vocab.build_runtime_vocab(
            verified, ["OpenAI", "Superlinear Academy"]
        )

        self.assertEqual(
            set(runtime), {"schema_version", "verified_candidates", "hotwords_context"}
        )
        self.assertEqual(runtime["schema_version"], 2)
        self.assertEqual(
            runtime["verified_candidates"],
            {"可信词": {"alternatives": ["正确词"], "hint": "人工确认"}},
        )
        self.assertNotIn("count", runtime["verified_candidates"]["可信词"])
        self.assertIn("Superlinear Academy", runtime["hotwords_context"])
        self.assertNotIn("single_char_unidirectional", runtime)

    def test_hotwords_require_an_explicit_reviewed_file(self) -> None:
        self.assertEqual(extract_channel_vocab.load_verified_hotwords(None), [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hotwords.txt"
            path.write_text(
                "# reviewed only\nOpenAI\n\nOpenAI\nSuperlinear Academy\n",
                encoding="utf-8",
            )
            hotwords = extract_channel_vocab.load_verified_hotwords(path)
        self.assertEqual(hotwords, ["OpenAI", "Superlinear Academy"])

    def test_verified_corrections_fail_closed_when_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrections.json"
            path.write_text('{"误词": {"alternatives": []}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-empty string alternatives"):
                extract_channel_vocab.load_existing_candidates(path)

    def test_generated_outputs_cannot_overwrite_reviewed_sources(self) -> None:
        source = Path("/tmp/verified_corrections.json")
        with self.assertRaisesRegex(ValueError, "would overwrite source"):
            extract_channel_vocab.validate_artifact_paths(
                {"verified_corrections": source},
                {"runtime_vocab": source},
            )

    def test_runtime_and_audit_outputs_must_differ(self) -> None:
        output = Path("/tmp/channel_vocab.json")
        with self.assertRaisesRegex(ValueError, "must differ"):
            extract_channel_vocab.validate_artifact_paths(
                {},
                {"runtime_vocab": output, "audit_output": output},
            )


if __name__ == "__main__":
    unittest.main()
