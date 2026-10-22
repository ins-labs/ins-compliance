"""Tests for tools/agent_trailer_check.py (issue #46, ADR-0003).

A PR targeting main must carry an `Agent: <label>` trailer in its body so the
squash-merge commit keeps authorship attribution. The check derives the
expected label from the head branch name, validated against registry labels.
"""
import importlib, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
atc = importlib.import_module("agent_trailer_check")

LABELS = ["karim", "dina", "gawaher", "damij"]


class LabelFromBranchTest(unittest.TestCase):
    def test_prefixed_branch(self):
        self.assertEqual(atc.label_from_branch("karim/agent-trailer-check-46", LABELS), "karim")

    def test_nested_prefix(self):
        self.assertEqual(atc.label_from_branch("dina/fix/thing", LABELS), "dina")

    def test_unknown_prefix(self):
        self.assertIsNone(atc.label_from_branch("random-thing", LABELS))

    def test_registry_miss(self):
        self.assertIsNone(atc.label_from_branch("nobody/work", LABELS))


class CheckPrBodyTest(unittest.TestCase):
    def test_ok_own_line(self):
        ok, detail = atc.check_pr_body("Fix the thing\n\nAgent: karim\n", "karim")
        self.assertTrue(ok, detail)

    def test_ok_with_other_trailers(self):
        ok, detail = atc.check_pr_body("body\n\nReviewed-by: samir\nAgent: karim\n", "karim")
        self.assertTrue(ok, detail)

    def test_fail_missing(self):
        ok, detail = atc.check_pr_body("Fix the thing\n\nno trailer here\n", "karim")
        self.assertFalse(ok)
        self.assertIn("karim", detail)

    def test_fail_wrong_agent(self):
        ok, detail = atc.check_pr_body("body\n\nAgent: dina\n", "karim")
        self.assertFalse(ok)

    def test_fail_inline_mention(self):
        ok, detail = atc.check_pr_body("see Agent: karim notes inline", "karim")
        self.assertFalse(ok)

    def test_case_insensitive_key(self):
        ok, detail = atc.check_pr_body("body\n\nagent: karim\n", "karim")
        self.assertTrue(ok, detail)

    def test_unknown_label_always_fails(self):
        ok, detail = atc.check_pr_body("body\n\nAgent: karim\n", None)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
