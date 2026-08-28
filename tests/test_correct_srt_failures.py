#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校对失败路径回归测试。

背景：codex CLI 曾因 Homebrew cask 载荷被清空而从 PATH 上消失，
call_codex_for_corrections 把异常吞掉返回 []，最终日志是
`corrections=0 api_errors=0` 且 exit 0——看起来像「校对通过但无需修正」，
实际整条精校完全没跑。这里锁住三件事：

  1. codex 缺失 → 硬失败，且不产出 corrected.srt
  2. codex 调用异常 → 计入 api_errors，不伪装成「零修正」
  3. CLI 入口失败时非零退出

运行：venv/bin/python -m unittest discover -s tests -v
"""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "tools" / "correct"))

import correct_srt  # noqa: E402

SAMPLE_SRT = """1
00:00:00,000 --> 00:00:03,000
我们在 Superlillian 学院讨论了刘佳老师的观点

2
00:00:03,000 --> 00:00:06,000
这个判断百分之百是对的
"""


class TempSrtCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.qwen = self.tmpdir / "ep.qwen.srt"
        self.qwen.write_text(SAMPLE_SRT, encoding="utf-8")
        self.corrected = self.tmpdir / "ep.corrected.srt"

    def tearDown(self):
        self._tmp.cleanup()


class TestCodexMissing(TempSrtCase):
    """codex 不在 PATH 上时必须硬失败，而不是静默产出未精校字幕。"""

    def test_ensure_codex_available_raises(self):
        with unittest.mock.patch.object(correct_srt.shutil, "which", return_value=None):
            with self.assertRaises(correct_srt.CodexUnavailableError) as cm:
                correct_srt.ensure_codex_available()
        # 报错要能直接指导修复
        self.assertIn("brew reinstall --cask codex", str(cm.exception))

    def test_ensure_codex_available_returns_path(self):
        with unittest.mock.patch.object(
            correct_srt.shutil, "which", return_value="/opt/homebrew/bin/codex"
        ):
            self.assertEqual(
                correct_srt.ensure_codex_available(), "/opt/homebrew/bin/codex"
            )

    def test_correct_file_raises_and_writes_nothing(self):
        with unittest.mock.patch.object(correct_srt.shutil, "which", return_value=None):
            with self.assertRaises(correct_srt.CodexUnavailableError):
                correct_srt.correct_file(self.qwen)
        self.assertFalse(
            self.corrected.exists(),
            "codex 缺失时不允许产出 corrected.srt（哪怕内容与输入相同）",
        )

    def test_precheck_runs_before_touching_input(self):
        """前置检查要早于文件读取——输入不存在时也应先报 codex 缺失。"""
        with unittest.mock.patch.object(correct_srt.shutil, "which", return_value=None):
            with self.assertRaises(correct_srt.CodexUnavailableError):
                correct_srt.correct_file(self.tmpdir / "不存在.qwen.srt")

    def test_cli_exits_non_zero(self):
        """端到端：codex 不在 PATH 上时 CLI 必须非零退出。"""
        env = {"PATH": "/nonexistent", "HOME": str(self.tmpdir)}
        proc = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "tools" / "correct" / "correct_srt.py"),
             str(self.qwen)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        self.assertNotEqual(proc.returncode, 0, "codex 缺失时不允许 exit 0")
        self.assertIn("字幕精校无法运行", proc.stdout + proc.stderr)
        self.assertFalse(self.corrected.exists())


class TestCodexCallFails(TempSrtCase):
    """codex 在，但调用失败（超时/非零退出/没写文件）时不能伪装成零修正。"""

    def test_api_error_counted(self):
        with unittest.mock.patch.object(
            correct_srt, "call_codex_file_based",
            side_effect=RuntimeError("codex 失败 (exit 1): boom"),
        ):
            parsed, api_errors = correct_srt.call_codex_for_corrections([], [])
        self.assertEqual(parsed, [])
        self.assertEqual(api_errors, 1, "调用失败必须计入 api_errors")

    def test_success_reports_zero_errors(self):
        payload = '[{"chunk_idx": 0, "original": "刘佳", "corrected": "刘嘉", "reason": "人名"}]'
        seen = {}

        def _fake(prompt, output_path, **kwargs):
            seen.update(kwargs)
            output_path.write_text(payload, encoding="utf-8")
            return payload

        with unittest.mock.patch.object(
            correct_srt, "call_codex_file_based", side_effect=_fake
        ):
            parsed, api_errors = correct_srt.call_codex_for_corrections([], [])
        self.assertEqual(api_errors, 0)
        self.assertEqual(parsed[0]["corrected"], "刘嘉")
        self.assertNotIn("cwd", seen)

    def test_malformed_reply_is_an_api_error(self):
        def _fake(prompt, output_path, **kwargs):
            output_path.write_text("not json", encoding="utf-8")
            return "not json"

        with unittest.mock.patch.object(
            correct_srt, "call_codex_file_based", side_effect=_fake
        ):
            parsed, api_errors = correct_srt.call_codex_for_corrections([], [])

        self.assertEqual(parsed, [])
        self.assertEqual(api_errors, 1)

    def test_non_list_reply_is_an_api_error(self):
        def _fake(prompt, output_path, **kwargs):
            output_path.write_text('{"corrections": []}', encoding="utf-8")
            return '{"corrections": []}'

        with unittest.mock.patch.object(
            correct_srt, "call_codex_file_based", side_effect=_fake
        ):
            _, api_errors = correct_srt.call_codex_for_corrections([], [])

        self.assertEqual(api_errors, 1)

    def test_longform_timeout_is_forwarded(self):
        seen = {}

        def _fake(prompt, output_path, **kwargs):
            seen.update(kwargs)
            output_path.write_text("[]", encoding="utf-8")
            return "[]"

        with unittest.mock.patch.object(
            correct_srt, "call_codex_file_based", side_effect=_fake
        ):
            _, api_errors = correct_srt.call_codex_for_corrections(
                [], [], timeout=1234
            )
        self.assertEqual(api_errors, 0)
        self.assertEqual(seen["timeout"], 1234)

    def test_episode_seeds_are_injected_as_context_not_rules(self):
        prompt = correct_srt.build_correction_prompt(
            [{"timestamp": "00:00:00,000 --> 00:00:01,000", "text": "嘉宾发言"}],
            [],
            episode_seeds=["梁津晶", "Orca"],
        )
        self.assertIn("本期人工确认的实体写法", prompt)
        self.assertIn("梁津晶、Orca", prompt)
        self.assertIn("不是盲目替换规则", prompt)

        no_seeds = correct_srt.build_correction_prompt([], [], episode_seeds=[])
        self.assertNotIn("本期人工确认的实体写法", no_seeds)

    def test_correct_file_produces_no_output_on_api_error(self):
        with unittest.mock.patch.object(
            correct_srt.shutil, "which", return_value="/opt/homebrew/bin/codex"
        ), unittest.mock.patch.object(
            correct_srt, "call_codex_file_based",
            side_effect=RuntimeError("codex 失败 (exit 1): boom"),
        ):
            result = correct_srt.correct_file(self.qwen)
        self.assertIsNone(result, "调用失败时 correct_file 必须返回 None")
        self.assertFalse(
            self.corrected.exists(),
            "调用失败时不允许产出 corrected.srt",
        )

    def test_correct_file_produces_no_output_on_malformed_reply(self):
        def _fake(prompt, output_path, **kwargs):
            output_path.write_text("not json", encoding="utf-8")
            return "not json"

        with unittest.mock.patch.object(
            correct_srt.shutil, "which", return_value="/opt/homebrew/bin/codex"
        ), unittest.mock.patch.object(
            correct_srt, "call_codex_file_based", side_effect=_fake
        ):
            result = correct_srt.correct_file(self.qwen)

        self.assertIsNone(result)
        self.assertFalse(self.corrected.exists())


class TestCorrectionsSurviveValidation(TempSrtCase):
    """
    候选词没命中时 flag_patterns 为空集，严格验证器会挡下每一条修正。
    v4 单次合并调用必须同时跑全文验证器，否则 corrections 恒为 0。
    """

    CODEX_REPLY = (
        '[{"chunk_idx": 0, "original": "刘佳", "corrected": "刘嘉", "reason": "人名同音字"},'
        ' {"chunk_idx": 0, "original": "Superlillian", "corrected": "Superlinear", "reason": "品牌拼写"}]'
    )

    def _run_with_reply(self, reply: str) -> Path | None:
        def _fake(prompt, output_path, **kwargs):
            output_path.write_text(reply, encoding="utf-8")
            return reply

        with unittest.mock.patch.object(
            correct_srt.shutil, "which", return_value="/opt/homebrew/bin/codex"
        ), unittest.mock.patch.object(
            correct_srt, "call_codex_file_based", side_effect=_fake
        ):
            return correct_srt.correct_file(self.qwen)

    def test_full_scan_corrections_are_applied(self):
        result = self._run_with_reply(self.CODEX_REPLY)
        self.assertIsNotNone(result)
        text = result.read_text(encoding="utf-8")
        self.assertIn("刘嘉", text)
        self.assertIn("Superlinear", text)
        self.assertNotIn("刘佳", text)
        self.assertNotIn("Superlillian", text)

    def test_merge_dedupes_by_chunk_and_original(self):
        strict = [{"chunk_idx": 0, "original": "刘佳", "corrected": "刘嘉"}]
        loose = [
            {"chunk_idx": 0, "original": "刘佳", "corrected": "刘嘉"},
            {"chunk_idx": 0, "original": "Superlillian", "corrected": "Superlinear"},
            {"chunk_idx": 1, "original": "刘佳", "corrected": "刘嘉"},
        ]
        merged = correct_srt.merge_corrections(
            strict, loose, ["占位文本" * 50, "占位文本" * 50]
        )
        self.assertEqual(
            [(c["chunk_idx"], c["original"]) for c in merged],
            [(0, "刘佳"), (0, "Superlillian"), (1, "刘佳")],
        )

    def test_empty_reply_yields_no_corrections(self):
        """codex 明确回 [] 时不该凭空造出修正。"""
        result = self._run_with_reply("[]")
        self.assertIsNotNone(result)
        self.assertIn("刘佳", result.read_text(encoding="utf-8"))


class TestApplyCorrections(unittest.TestCase):
    """
    每个修正必须绑定模型核对过的 chunk；同样的词在其他语境不能被全文误改。
    """

    @staticmethod
    def _chunks(texts: list[str]) -> list[dict]:
        return [{"timestamp": "", "text": t} for t in texts]

    def test_multi_char_replaced_only_in_targeted_chunks(self):
        chunks = self._chunks([f"第{i}句提到了 Superlillian" for i in range(1, 6)])
        out, n = correct_srt.apply_corrections(
            chunks,
            [
                {"chunk_idx": 0, "original": "Superlillian", "corrected": "Superlinear"},
                {"chunk_idx": 2, "original": "Superlillian", "corrected": "Superlinear"},
            ],
        )
        self.assertEqual(n, 2)
        self.assertIn("Superlinear", out[0]["text"])
        self.assertIn("Superlillian", out[1]["text"])
        self.assertIn("Superlinear", out[2]["text"])

    def test_multiple_hits_in_one_chunk(self):
        chunks = self._chunks(["刘佳老师和刘佳同学"])
        out, n = correct_srt.apply_corrections(
            chunks, [{"chunk_idx": 0, "original": "刘佳", "corrected": "刘嘉"}]
        )
        self.assertEqual(n, 2)
        self.assertEqual(out[0]["text"], "刘嘉老师和刘嘉同学")

    def test_single_char_only_targeted_chunk(self):
        """单字全局替换会误伤——「他」在别处可能本来就是对的。"""
        chunks = self._chunks(["他说的对", "他是个好人", "他走了"])
        out, n = correct_srt.apply_corrections(
            chunks, [{"chunk_idx": 0, "original": "他", "corrected": "她"}]
        )
        self.assertEqual(n, 1)
        self.assertEqual([c["text"] for c in out], ["她说的对", "他是个好人", "他走了"])

    def test_input_chunks_not_mutated(self):
        chunks = self._chunks(["提到了 Superlillian"])
        correct_srt.apply_corrections(
            chunks,
            [{"chunk_idx": 0, "original": "Superlillian", "corrected": "Superlinear"}],
        )
        self.assertEqual(chunks[0]["text"], "提到了 Superlillian")

    def test_replacement_count_differs_from_correction_count(self):
        """汇总日志要能区分「2 条修正」和「6 处替换」。"""
        chunks = self._chunks(["刘佳 Superlillian", "刘佳 Superlillian", "刘佳 Superlillian"])
        corrs = [
            {"chunk_idx": 0, "original": "刘佳", "corrected": "刘嘉"},
            {"chunk_idx": 1, "original": "Superlillian", "corrected": "Superlinear"},
        ]
        _, n = correct_srt.apply_corrections(chunks, corrs)
        self.assertEqual(len(corrs), 2)
        self.assertEqual(n, 2)

    def test_ambiguous_word_is_not_changed_in_another_context(self):
        chunks = self._chunks(["人在时代里沉浮", "他最后选择了沉浮"])
        output, count = correct_srt.apply_corrections(
            chunks,
            [{"chunk_idx": 1, "original": "沉浮", "corrected": "臣服"}],
        )
        self.assertEqual(count, 1)
        self.assertEqual(output[0]["text"], "人在时代里沉浮")
        self.assertEqual(output[1]["text"], "他最后选择了臣服")

    def test_verified_numeric_candidate_accepts_only_reviewed_alternative(self):
        chunk_texts = ["从五到十个人"]
        flags = [
            {
                "chunk_idx": 0,
                "found": "到十",
                "alternatives": ["到10"],
                "hint": "数字格式候选",
                "is_single": False,
            }
        ]
        accepted = correct_srt.validate_corrections(
            [
                {
                    "chunk_idx": 0,
                    "original": "到十",
                    "corrected": "到10",
                }
            ],
            chunk_texts,
            flags,
        )
        rejected = correct_srt.validate_corrections(
            [
                {
                    "chunk_idx": 0,
                    "original": "到十",
                    "corrected": "到99",
                }
            ],
            chunk_texts,
            flags,
        )
        loose_rejected = correct_srt.validate_corrections_full_scan(
            [
                {
                    "chunk_idx": 0,
                    "original": "到十",
                    "corrected": "到99",
                }
            ],
            chunk_texts,
            excluded_flag_patterns={0: {"到十"}},
        )
        merged_rejected = correct_srt.merge_corrections(
            rejected, loose_rejected, chunk_texts
        )
        substring_bypass = correct_srt.validate_corrections_full_scan(
            [
                {
                    "chunk_idx": 0,
                    "original": "十",
                    "corrected": "九",
                }
            ],
            chunk_texts,
            excluded_flag_patterns={0: {"到十"}},
        )
        self.assertEqual(accepted[0]["corrected"], "到10")
        self.assertEqual(merged_rejected, [])
        self.assertEqual(substring_bypass, [])

    def test_contextual_number_phrases_are_not_blindly_rewritten(self):
        chunks = self._chunks(["从五到十个人，幺幺是他的昵称"])
        output, count = correct_srt.apply_format_rules(chunks)
        self.assertEqual(count, 0)
        self.assertEqual(output[0]["text"], chunks[0]["text"])

    def test_full_scan_allows_local_brand_spelling_fix(self):
        corrections = correct_srt.validate_corrections_full_scan(
            [
                {
                    "chunk_idx": 0,
                    "original": "Superlillian",
                    "corrected": "Superlinear",
                }
            ],
            ["我们在 Superlillian 学院讨论"],
        )
        self.assertEqual(corrections[0]["corrected"], "Superlinear")

    def test_full_scan_rejects_large_english_deletion_or_rewrite(self):
        chunk = "this is a long phrase 不应该被删掉"
        parsed = [
            {
                "chunk_idx": 0,
                "original": "this is a long phrase",
                "corrected": "X",
            },
            {
                "chunk_idx": 0,
                "original": "long phrase",
                "corrected": "new product",
            },
        ]
        self.assertEqual(
            correct_srt.validate_corrections_full_scan(parsed, [chunk]), []
        )

    def test_aggregate_edit_budget_is_actually_enforced(self):
        corrections = [
            {
                "chunk_idx": index,
                "original": chr(0x4E00 + index),
                "corrected": chr(0x5000 + index),
            }
            for index in range(30)
        ]
        limited = correct_srt._limit_by_edit_budget(corrections, total_chars=100)
        changed = sum(
            max(len(item["original"]), len(item["corrected"])) for item in limited
        )
        self.assertLessEqual(changed, 20)
        self.assertEqual(len(limited), 20)


class TestQualityGate(TempSrtCase):
    """process_video 的质量门：corrected 与 qwen 文本一致时必须告警。"""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(_REPO_ROOT / "tools"))
        import process_video  # noqa: E402
        self.process_video = process_video

    def _capture(self, qwen: Path, corrected: Path, stats: dict) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.process_video.warn_if_uncorrected(qwen, corrected, stats)
        return buf.getvalue()

    def test_warns_when_identical(self):
        self.corrected.write_text(SAMPLE_SRT, encoding="utf-8")
        out = self._capture(self.qwen, self.corrected, {"corrections": 0, "fmt": 0})
        self.assertIn("质量门", out)
        self.assertIn("文本完全一致", out)

    def test_silent_when_corrections_applied(self):
        self.corrected.write_text(
            SAMPLE_SRT.replace("刘佳", "刘嘉").replace("Superlillian", "Superlinear"),
            encoding="utf-8",
        )
        self.assertEqual(
            self._capture(self.qwen, self.corrected, {"corrections": 2, "fmt": 0}), ""
        )

    def test_warns_when_only_format_rules_fired(self):
        """
        真实事故形态：codex 没跑（corrections=0），但规则层改了格式，
        两文件内容因此不同。基于内容比对的质量门会漏掉这种情况。
        """
        self.corrected.write_text(
            SAMPLE_SRT.replace("百分之百", "100%"), encoding="utf-8"
        )
        out = self._capture(self.qwen, self.corrected, {"corrections": 0, "fmt": 1})
        self.assertIn("质量门", out)
        self.assertIn("规则层格式规范化", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
