import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.generate_article import (
    _read_editorial_notes,
    _read_guest_profile,
    _read_highlights,
    generate_article,
    load_writing_skill_context,
    resolve_article_type,
    resolve_writing_skill,
    WritingSkillSpec,
)
from tools import atomic_delivery


class GenerateArticleContextTests(unittest.TestCase):
    def test_article_bundle_commit_failure_restores_all_previous_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt_path = root / "episode.final.srt"
            srt_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n当前内容\n",
                encoding="utf-8",
            )
            process_dir = root / "episode_process"
            process_dir.mkdir()
            article = root / "episode.article.md"
            brief = process_dir / "episode.article-brief.md"
            context = process_dir / "episode.article-context.json"
            snapshot = process_dir / "episode.writing-skill.md"
            old = {
                article: "old article",
                brief: "old brief",
                context: "old context",
                snapshot: "old snapshot",
            }
            for path, value in old.items():
                path.write_text(value, encoding="utf-8")
            writing_skill = root / "interview.SKILL.md"
            writing_skill.write_text(
                "---\nname: expert-interview-article\n---\nCURRENT SKILL",
                encoding="utf-8",
            )

            def fake_call(_prompt, output_path, **_kwargs):
                output_path.write_text("new generated content", encoding="utf-8")
                return "new generated content"

            real_replace = atomic_delivery._replace_path

            def fail_context_commit(source, destination):
                if (
                    destination == context.resolve()
                    and ".backup." not in source.name
                ):
                    raise OSError("simulated context commit failure")
                return real_replace(source, destination)

            with (
                patch("tools.generate_article.call_content_file_based", fake_call),
                patch.object(
                    atomic_delivery,
                    "_replace_path",
                    side_effect=fail_context_commit,
                ),
            ):
                with self.assertRaisesRegex(OSError, "context commit failure"):
                    generate_article(
                        srt_path,
                        article_type="interview",
                        surface="companion",
                        writing_skill_path=writing_skill,
                    )

            for path, value in old.items():
                self.assertEqual(path.read_text(encoding="utf-8"), value)

    def test_writing_skill_context_uses_bundled_fallback_on_fresh_clone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_path = root / "bundled.md"
            skill_path.write_text("# Interview writing guidance", encoding="utf-8")

            with (
                patch.dict(
                    "tools.generate_article._WRITING_SKILLS",
                    {
                        "interview": WritingSkillSpec(
                            name="test-interview-skill",
                            label="访谈文章主责：test-interview-skill",
                            bundled_path=skill_path,
                        )
                    },
                    clear=True,
                ),
                patch.dict(os.environ, {}, clear=True),
                patch("tools.generate_article.Path.home", return_value=root / "home"),
            ):
                context = load_writing_skill_context("interview")

            self.assertIn("test-interview-skill", context)
            self.assertIn("Interview writing guidance", context)
            self.assertIn("没有加载其中按需引用的外部文件", context)

    def test_live_installed_skill_wins_and_is_identified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled_path = root / "bundled.md"
            bundled_path.write_text("BUNDLED", encoding="utf-8")
            override_root = root / "live"
            live_path = override_root / "test-interview-skill" / "SKILL.md"
            live_path.parent.mkdir(parents=True)
            live_path.write_text("LIVE_CURRENT_SKILL", encoding="utf-8")

            with (
                patch.dict(
                    "tools.generate_article._WRITING_SKILLS",
                    {
                        "interview": WritingSkillSpec(
                            name="test-interview-skill",
                            label="interview",
                            bundled_path=bundled_path,
                        )
                    },
                    clear=True,
                ),
                patch.dict(
                    os.environ,
                    {"LIZHENG_WRITING_SKILLS_DIR": str(override_root)},
                    clear=True,
                ),
                patch("tools.generate_article.Path.home", return_value=root / "home"),
            ):
                resolved = resolve_writing_skill("interview")

            self.assertEqual(resolved.source, "environment")
            self.assertEqual(resolved.path, live_path)
            self.assertEqual(resolved.content, "LIVE_CURRENT_SKILL")

    def test_explicit_skill_snapshot_can_be_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled_path = root / "bundled.md"
            bundled_path.write_text("BUNDLED", encoding="utf-8")
            snapshot_path = root / "episode.writing-skill.md"
            snapshot_path.write_text(
                "---\nname: test-interview-skill\n---\nPINNED_SNAPSHOT",
                encoding="utf-8",
            )

            with patch.dict(
                "tools.generate_article._WRITING_SKILLS",
                {
                    "interview": WritingSkillSpec(
                        name="test-interview-skill",
                        label="interview",
                        bundled_path=bundled_path,
                    )
                },
                clear=True,
            ):
                resolved = resolve_writing_skill(
                    "interview",
                    explicit_path=snapshot_path,
                )

            self.assertEqual(resolved.source, "explicit")
            self.assertEqual(resolved.path, snapshot_path)
            self.assertIn("PINNED_SNAPSHOT", resolved.content)

    def test_explicit_skill_snapshot_rejects_type_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "interview.writing-skill.md"
            snapshot_path.write_text(
                "---\nname: test-interview-skill\n---\nINTERVIEW",
                encoding="utf-8",
            )
            monologue_bundle = root / "monologue.md"
            monologue_bundle.write_text("MONOLOGUE", encoding="utf-8")

            with patch.dict(
                "tools.generate_article._WRITING_SKILLS",
                {
                    "monologue": WritingSkillSpec(
                        name="test-monologue-skill",
                        label="monologue",
                        bundled_path=monologue_bundle,
                    )
                },
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "类型不匹配"):
                    resolve_writing_skill(
                        "monologue",
                        explicit_path=snapshot_path,
                    )

    def test_editorial_notes_prefers_episode_process_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt_path = root / "episode.final.srt"
            srt_path.write_text("", encoding="utf-8")
            process_dir = root / "episode_process"
            process_dir.mkdir()
            (process_dir / "episode.editorial-notes.md").write_text(
                "跨段观察", encoding="utf-8"
            )
            (root / "article-notes.md").write_text("fallback", encoding="utf-8")

            notes = _read_editorial_notes(srt_path, root, "episode")

            self.assertEqual(notes, "跨段观察")

    def test_unprefixed_episode_metadata_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt_path = root / "episode.final.srt"
            srt_path.write_text("", encoding="utf-8")
            (root / "guest-profile.md").write_text("另一集嘉宾", encoding="utf-8")
            (root / "editorial-notes.md").write_text("另一集观察", encoding="utf-8")

            self.assertEqual(_read_guest_profile(srt_path, root, "episode"), "")
            self.assertEqual(_read_editorial_notes(srt_path, root, "episode"), "")

    def test_delivery_highlights_win_over_stale_workspace_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt_path = root / "episode.final.srt"
            srt_path.write_text("", encoding="utf-8")
            process_dir = root / "episode_process"
            process_dir.mkdir()
            (process_dir / "episode.highlights.md").write_text(
                "STALE", encoding="utf-8"
            )
            (root / "episode.highlights.md").write_text("CURRENT", encoding="utf-8")

            highlights = _read_highlights(
                srt_path,
                root,
                "episode",
                workspace_dir=process_dir,
            )

            self.assertEqual(highlights, "CURRENT")

    def test_failed_upstream_can_disable_stale_highlight_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt_path = root / "episode.final.srt"
            srt_path.write_text("", encoding="utf-8")
            (root / "episode.highlights.md").write_text(
                "STALE", encoding="utf-8"
            )

            highlights = _read_highlights(
                srt_path,
                root,
                "episode",
                discover_highlights=False,
            )

            self.assertEqual(highlights, "")

    def test_auto_type_uses_highlights_marker(self) -> None:
        article_type, source = resolve_article_type(
            "auto",
            "## 视频类型和主发言人\n访谈，两位发言者",
            "",
            "",
        )

        self.assertEqual(article_type, "interview")
        self.assertEqual(source, "highlights")

    def test_generate_article_injects_only_selected_skill_and_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt_path = root / "episode.final.srt"
            srt_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8"
            )
            interview_skill = root / "interview.md"
            interview_skill.write_text("INTERVIEW_ONLY", encoding="utf-8")
            monologue_skill = root / "monologue.md"
            monologue_skill.write_text("MONOLOGUE_ONLY", encoding="utf-8")
            prompts: list[str] = []

            def fake_call(prompt: str, output_path: Path, model: str) -> str:
                prompts.append(prompt)
                content = "brief" if "article-brief" in output_path.name else "article"
                output_path.write_text(content, encoding="utf-8")
                return content

            with (
                patch.dict(
                    "tools.generate_article._WRITING_SKILLS",
                    {
                        "interview": WritingSkillSpec(
                            name="test-interview-skill",
                            label="interview",
                            bundled_path=interview_skill,
                        ),
                        "monologue": WritingSkillSpec(
                            name="test-monologue-skill",
                            label="monologue",
                            bundled_path=monologue_skill,
                        ),
                    },
                    clear=True,
                ),
                patch.dict(os.environ, {}, clear=True),
                patch("tools.generate_article.Path.home", return_value=root / "home"),
                patch("tools.generate_article.call_content_file_based", fake_call),
            ):
                generate_article(
                    srt_path,
                    article_type="interview",
                    surface="community",
                )

            self.assertEqual(len(prompts), 2)
            for prompt in prompts:
                self.assertIn("INTERVIEW_ONLY", prompt)
                self.assertNotIn("MONOLOGUE_ONLY", prompt)
                self.assertIn("Surface：community", prompt)
                self.assertIn("本期是访谈", prompt)
                self.assertNotIn("本期是单口", prompt)
            self.assertIn("provisional hypotheses", prompts[1])
            context = json.loads(
                (root / "episode_process" / "episode.article-context.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(context["writing_skill"]["source"], "bundled")
            self.assertEqual(
                context["writing_skill"]["sha256"],
                hashlib.sha256(b"INTERVIEW_ONLY").hexdigest(),
            )
            snapshot_path = Path(context["writing_skill"]["snapshot_path"])
            self.assertEqual(snapshot_path.read_text(encoding="utf-8"), "INTERVIEW_ONLY")

    def test_explicit_monologue_ignores_stale_interview_auxiliaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt_path = root / "episode.final.srt"
            srt_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nCURRENT_MONOLOGUE\n",
                encoding="utf-8",
            )
            (root / "episode.speaker_labeled.md").write_text(
                "STALE_INTERVIEW_TRANSCRIPT", encoding="utf-8"
            )
            (root / "episode.guest-profile.md").write_text(
                "OTHER_EPISODE_GUEST", encoding="utf-8"
            )
            skill_path = root / "monologue.md"
            skill_path.write_text("MONOLOGUE_SKILL", encoding="utf-8")
            prompts: list[str] = []

            def fake_call(prompt: str, output_path: Path, model: str) -> str:
                prompts.append(prompt)
                output_path.write_text("generated", encoding="utf-8")
                return "generated"

            with (
                patch.dict(
                    "tools.generate_article._WRITING_SKILLS",
                    {
                        "monologue": WritingSkillSpec(
                            name="test-monologue-skill",
                            label="monologue",
                            bundled_path=skill_path,
                        )
                    },
                    clear=True,
                ),
                patch.dict(os.environ, {}, clear=True),
                patch("tools.generate_article.Path.home", return_value=root / "home"),
                patch("tools.generate_article.call_content_file_based", fake_call),
            ):
                generate_article(
                    srt_path,
                    article_type="monologue",
                    surface="article",
                )

            self.assertEqual(len(prompts), 2)
            for prompt in prompts:
                self.assertIn("CURRENT_MONOLOGUE", prompt)
                self.assertNotIn("STALE_INTERVIEW_TRANSCRIPT", prompt)
                self.assertNotIn("OTHER_EPISODE_GUEST", prompt)
                self.assertIn("本期是单口", prompt)
                self.assertNotIn("本期是访谈", prompt)
                self.assertNotIn("嘉宾如何理解", prompt)

    def test_legacy_fourth_positional_argument_remains_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt_path = root / "episode.final.srt"
            srt_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8"
            )
            skill_path = root / "interview.md"
            skill_path.write_text("INTERVIEW", encoding="utf-8")

            def fake_call(prompt: str, output_path: Path, model: str) -> str:
                output_path.write_text("generated", encoding="utf-8")
                return "generated"

            with (
                patch.dict(
                    "tools.generate_article._WRITING_SKILLS",
                    {
                        "interview": WritingSkillSpec(
                            name="test-interview-skill",
                            label="interview",
                            bundled_path=skill_path,
                        )
                    },
                    clear=True,
                ),
                patch.dict(os.environ, {}, clear=True),
                patch("tools.generate_article.Path.home", return_value=root / "home"),
                patch("tools.generate_article.call_content_file_based", fake_call),
            ):
                result = generate_article(
                    srt_path,
                    0,
                    root,
                    "legacy-stem",
                    article_type="interview",
                    surface="community",
                )

            self.assertEqual(result, root / "legacy-stem.article.md")

    def test_failed_rerun_preserves_previous_article_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            process_dir = root / "episode_process"
            process_dir.mkdir()
            srt_path = root / "episode.final.srt"
            srt_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8"
            )
            previous = {
                root / "episode.article.md": "OLD_ARTICLE",
                process_dir / "episode.article-brief.md": "OLD_BRIEF",
                process_dir / "episode.article-context.json": "OLD_CONTEXT",
                process_dir / "episode.writing-skill.md": "OLD_SKILL",
            }
            for path, content in previous.items():
                path.write_text(content, encoding="utf-8")
            skill_path = root / "interview.md"
            skill_path.write_text("NEW_SKILL", encoding="utf-8")

            with (
                patch.dict(
                    "tools.generate_article._WRITING_SKILLS",
                    {
                        "interview": WritingSkillSpec(
                            name="test-interview-skill",
                            label="interview",
                            bundled_path=skill_path,
                        )
                    },
                    clear=True,
                ),
                patch.dict(os.environ, {}, clear=True),
                patch("tools.generate_article.Path.home", return_value=root / "home"),
                patch(
                    "tools.generate_article.call_content_file_based",
                    side_effect=RuntimeError("brief failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "brief failed"):
                    generate_article(
                        srt_path,
                        article_type="interview",
                        surface="community",
                    )

            for path, content in previous.items():
                self.assertEqual(path.read_text(encoding="utf-8"), content)
            self.assertEqual(list(root.glob(".episode.*")), [])
            self.assertEqual(list(process_dir.glob(".episode.*")), [])


if __name__ == "__main__":
    unittest.main()
