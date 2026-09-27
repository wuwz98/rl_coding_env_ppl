import csv
import json
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

from discover_candidates import (
    DiscoveryError,
    csv_row,
    discover,
    extract_closing_issue_references,
    file_features,
    in_merged_range,
    is_test_path,
    issue_text_signals,
    parse_date_boundary,
    parse_json_object,
    parse_repo,
    write_outputs,
)


class FakeGitHubClient:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("get", path, params))
        return self.values[path]

    def get_all(self, path, params=None, max_pages=0):
        del max_pages
        self.calls.append(("get_all", path, params))
        return self.values[path]

    def evidence(self):
        return {"network_requests": 0, "cache_hits": 0}


def pull_fixture():
    return {
        "number": 720,
        "html_url": "https://github.com/more-itertools/more-itertools/pull/720",
        "title": "Fix behavior",
        "body": "Closes #719",
        "user": {"login": "contributor"},
        "author_association": "CONTRIBUTOR",
        "labels": [{"name": "bug"}],
        "created_at": "2023-05-21T14:02:45Z",
        "updated_at": "2023-05-21T19:15:14Z",
        "closed_at": "2023-05-21T17:47:12Z",
        "merged_at": "2023-05-21T17:47:12Z",
        "base": {"sha": "a" * 40},
        "head": {"sha": "b" * 40},
        "merge_commit_sha": "c" * 40,
        "commits": 3,
        "comments": 2,
        "review_comments": 3,
        "changed_files": 2,
        "additions": 17,
        "deletions": 11,
    }


class ParsingTests(unittest.TestCase):
    def test_parse_repo(self):
        self.assertEqual(
            parse_repo("https://github.com/more-itertools/more-itertools.git"),
            "more-itertools/more-itertools",
        )
        with self.assertRaises(DiscoveryError):
            parse_repo("not a repo")

    def test_extracts_only_explicit_closing_references(self):
        references = extract_closing_issue_references(
            "\n".join(
                [
                    "Related to #1",
                    "Closes #2",
                    "fixes more-itertools/more-itertools#3",
                    "Resolved https://github.com/other/project/issues/4",
                    "Closes #2",
                ]
            ),
            "more-itertools/more-itertools",
        )
        self.assertEqual(
            [(item["repo"], item["number"]) for item in references],
            [
                ("more-itertools/more-itertools", 2),
                ("more-itertools/more-itertools", 3),
                ("other/project", 4),
            ],
        )
        self.assertEqual([item["is_local"] for item in references], [True, True, False])

    def test_path_policy_matches_lab02(self):
        self.assertTrue(is_test_path("tests/test_more.py"))
        self.assertTrue(is_test_path("pkg/widget_test.py"))
        self.assertFalse(is_test_path("more_itertools/more.py"))

    def test_date_boundaries_are_inclusive(self):
        since = parse_date_boundary("2023-05-21", end=False)
        until = parse_date_boundary("2023-05-21", end=True)
        self.assertEqual(since.tzinfo, timezone.utc)
        self.assertTrue(in_merged_range("2023-05-21T23:59:59Z", since, until))
        self.assertFalse(in_merged_range("2023-05-22T00:00:00Z", since, until))

    def test_parse_fenced_json_object(self):
        self.assertEqual(parse_json_object("```json\n{\"label\": \"CLEAR\"}\n```"), {
            "label": "CLEAR"
        })


class FeatureTests(unittest.TestCase):
    def test_file_features_split_source_and_tests(self):
        features = file_features(
            [
                {
                    "filename": "pkg/core.py",
                    "status": "modified",
                    "additions": 8,
                    "deletions": 3,
                    "changes": 11,
                },
                {
                    "filename": "tests/test_core.py",
                    "status": "modified",
                    "additions": 6,
                    "deletions": 0,
                    "changes": 6,
                },
            ],
            expected_count=2,
        )
        self.assertTrue(features["separable_by_lab02_policy"])
        self.assertEqual(features["source"]["changed_lines"], 11)
        self.assertEqual(features["tests"]["changed_lines"], 6)
        self.assertTrue(features["complete"])

    def test_issue_signals_do_not_claim_semantic_clarity(self):
        signals = issue_text_signals(
            "Behavior is unclear or wrong",
            "It currently returns A, but perhaps it should return B?",
        )
        self.assertTrue(signals["mentions_ambiguity"])
        self.assertTrue(signals["has_observed_behavior_language"])
        self.assertNotIn("label", signals)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.repo = "more-itertools/more-itertools"
        self.pull = pull_fixture()
        self.values = {
            "/repos/{}".format(self.repo): {
                "full_name": self.repo,
                "html_url": "https://github.com/{}".format(self.repo),
                "description": "Iterator tools",
                "default_branch": "master",
                "language": "Python",
                "license": {"spdx_id": "MIT"},
                "stargazers_count": 4000,
                "fork": False,
                "archived": False,
                "size": 5000,
            },
            "/repos/{}/pulls/720".format(self.repo): self.pull,
            "/repos/{}/pulls/720/files".format(self.repo): [
                {
                    "filename": "more_itertools/more.py",
                    "status": "modified",
                    "additions": 11,
                    "deletions": 10,
                    "changes": 21,
                },
                {
                    "filename": "tests/test_more.py",
                    "status": "modified",
                    "additions": 6,
                    "deletions": 1,
                    "changes": 7,
                },
            ],
            "/repos/{}/issues/719".format(self.repo): {
                "number": 719,
                "html_url": "https://github.com/{}/issues/719".format(self.repo),
                "title": "Behavior is unclear",
                "body": "It may mean input window or output window.",
                "user": {"login": "reporter"},
                "author_association": "CONTRIBUTOR",
                "state": "closed",
                "state_reason": "completed",
                "labels": [],
                "created_at": "2023-05-19T18:27:21Z",
                "updated_at": "2023-05-21T17:47:13Z",
                "closed_at": "2023-05-21T17:47:13Z",
                "comments": 1,
            },
        }

    def test_discover_emits_candidate_not_verified_task(self):
        client = FakeGitHubClient(self.values)
        candidates, rejected, summary = discover(
            client,
            self.repo,
            pr_numbers=[720],
            comment_mode="counts",
        )
        self.assertEqual(rejected, [])
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["status"], "DISCOVERED")
        self.assertEqual(candidate["verification_status"], "NOT_RUN")
        self.assertEqual(candidate["semantic_review"]["status"], "NOT_ASSESSED")
        self.assertEqual(candidate["patch"]["source"]["changed_lines"], 21)
        self.assertEqual(summary["candidate_count"], 1)
        self.assertIn("only Lab 02 execution", summary["result_contract"])

    def test_unseparable_pr_is_rejected_without_fetching_issue(self):
        self.values["/repos/{}/pulls/720/files".format(self.repo)] = [
            {
                "filename": "docs/guide.md",
                "status": "modified",
                "additions": 3,
                "deletions": 0,
                "changes": 3,
            }
        ]
        self.pull["changed_files"] = 1
        client = FakeGitHubClient(self.values)
        candidates, rejected, _ = discover(
            client,
            self.repo,
            pr_numbers=[720],
            comment_mode="counts",
        )
        self.assertEqual(candidates, [])
        self.assertEqual(rejected[0]["reason"]["code"], "PATCH_NOT_SEPARABLE")
        issue_path = "/repos/{}/issues/719".format(self.repo)
        self.assertFalse(any(call[1] == issue_path for call in client.calls))

    def test_outputs_include_jsonl_and_flat_csv(self):
        client = FakeGitHubClient(self.values)
        candidates, rejected, summary = discover(
            client,
            self.repo,
            pr_numbers=[720],
            comment_mode="counts",
        )
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            write_outputs(run_dir, candidates, rejected, summary)
            parsed = json.loads((run_dir / "candidates.jsonl").read_text())
            self.assertEqual(parsed["candidate_id"], candidates[0]["candidate_id"])
            with (run_dir / "candidates.csv").open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]["source_changed_lines"], "21")
            saved_summary = json.loads((run_dir / "summary.json").read_text())
            self.assertEqual(saved_summary["outputs"]["summary"], "summary.json")

    def test_csv_defaults_semantics_to_not_assessed(self):
        client = FakeGitHubClient(self.values)
        candidates, _, _ = discover(
            client,
            self.repo,
            pr_numbers=[720],
            comment_mode="counts",
        )
        row = csv_row(candidates[0])
        self.assertEqual(row["original_issue_clarity"], "NOT_ASSESSED")


if __name__ == "__main__":
    unittest.main()
