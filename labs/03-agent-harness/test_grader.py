import tempfile
import unittest
from pathlib import Path

from grader import evaluate_report, load_contract, patch_paths


HERE = Path(__file__).resolve().parent
TASK_DIR = (
    HERE.parent
    / "02-pr-task-compiler"
    / "tasks"
    / "more-itertools__more-itertools-issue-719-pr-720"
)


class PatchPolicyTests(unittest.TestCase):
    def test_empty_patch_has_no_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            patch = Path(temporary) / "empty.patch"
            patch.write_text("", encoding="utf-8")
            self.assertEqual(patch_paths(patch), [])

    def test_parses_touched_path(self):
        text = """diff --git a/pkg/widget.py b/pkg/widget.py
index 7898192..6178079 100644
--- a/pkg/widget.py
+++ b/pkg/widget.py
@@ -1 +1 @@
-a
+b
"""
        with tempfile.TemporaryDirectory() as temporary:
            patch = Path(temporary) / "candidate.patch"
            patch.write_text(text, encoding="utf-8")
            self.assertEqual(patch_paths(patch), ["pkg/widget.py"])


class ResultPolicyTests(unittest.TestCase):
    def setUp(self):
        self.contract = {
            "expected_ids": ["bug", "old"],
            "f2p": ["bug"],
            "p2p": ["old"],
        }

    def test_complete_pass_gets_reward_one(self):
        report = {
            "complete": True,
            "tests_run": 2,
            "counts": {"pass": 2},
            "tests": [
                {"id": "bug", "status": "pass"},
                {"id": "old", "status": "pass"},
            ],
        }
        result = evaluate_report(self.contract, report, 0)
        self.assertEqual(result["reward"], 1)
        self.assertEqual(result["verdict"], "PASS")

    def test_f2p_failure_gets_reward_zero(self):
        report = {
            "complete": True,
            "tests_run": 2,
            "counts": {"pass": 1, "fail": 1},
            "tests": [
                {"id": "bug", "status": "fail"},
                {"id": "old", "status": "pass"},
            ],
        }
        result = evaluate_report(self.contract, report, 1)
        self.assertEqual(result["reward"], 0)
        self.assertEqual(result["f2p_not_passing"], ["bug"])

    def test_missing_required_test_gets_reward_zero(self):
        report = {
            "complete": True,
            "tests_run": 1,
            "counts": {"pass": 1},
            "tests": [{"id": "bug", "status": "pass"}],
        }
        result = evaluate_report(self.contract, report, 0)
        self.assertEqual(result["reward"], 0)
        self.assertEqual(result["missing_required_tests"], ["old"])

    def test_lab02_contract_has_f2p_and_p2p(self):
        contract = load_contract(TASK_DIR)
        self.assertEqual(
            contract["f2p"],
            ["tests.test_more.UniqueInWindowTests.test_basic"],
        )
        self.assertEqual(len(contract["p2p"]), 733)
        self.assertEqual(contract["allowed_paths"], ["more_itertools/more.py"])


if __name__ == "__main__":
    unittest.main()
