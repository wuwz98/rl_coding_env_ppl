import unittest

from compile_task import Reject, is_test_path, parse_repo, pr_links_issue


class HelperTests(unittest.TestCase):
    def test_parse_repo(self):
        self.assertEqual(
            parse_repo("more-itertools/more-itertools"),
            (
                "more-itertools/more-itertools",
                "https://github.com/more-itertools/more-itertools.git",
            ),
        )

    def test_parse_repo_rejects_non_github_input(self):
        with self.assertRaises(Reject):
            parse_repo("not-a-repository")

    def test_pr_links_issue(self):
        self.assertTrue(pr_links_issue("Closes #719", 719))
        self.assertFalse(pr_links_issue("Related to #719", 719))
        self.assertFalse(pr_links_issue("Closes #718", 719))

    def test_test_path_policy(self):
        self.assertTrue(is_test_path("tests/test_more.py"))
        self.assertTrue(is_test_path("pkg/widget_test.py"))
        self.assertFalse(is_test_path("more_itertools/more.py"))


if __name__ == "__main__":
    unittest.main()
