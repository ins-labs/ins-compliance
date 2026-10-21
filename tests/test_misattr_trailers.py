"""Misattributed Agent trailers on squash merges (issue #36). Pure parse
and compare helpers; the run layer fetches PR head branches read-only."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import due_backfill as db


class ParseTest(unittest.TestCase):
    def test_pr_number_from_subject(self):
        self.assertEqual(db.pr_number("audit: add mode (closes #32) (#33)"), 33)
        self.assertEqual(db.pr_number("#251: healthcheck dedupe (#252)"), 252)

    def test_pr_number_absent(self):
        self.assertIsNone(db.pr_number("plain commit, no pr"))
        self.assertIsNone(db.pr_number("refs #32 but no merge suffix"))

    def test_trailer_label(self):
        self.assertEqual(db.trailer_label("subject\n\nAgent: karim\n"), "karim")
        self.assertEqual(db.trailer_label("s\n\nAgent: agent:gawaher\n"), "gawaher")

    def test_trailer_label_absent(self):
        self.assertIsNone(db.trailer_label("no trailer here"))

    def test_branch_agent(self):
        self.assertEqual(db.branch_agent("karim/audit-trailers"), "karim")
        self.assertEqual(db.branch_agent("kaleem/251-healthcheck-shared-slot"), "kaleem")

    def test_branch_agent_unowned(self):
        self.assertIsNone(db.branch_agent("main"))
        self.assertIsNone(db.branch_agent("fix-thing"))


class MisattrTest(unittest.TestCase):
    def test_mismatch_flagged(self):
        row = db.misattr_row("ins-compliance", "51e0939d" + "0" * 32,
                             "audit: add mode (closes #32) (#33)\n\nAgent: agent:gawaher\n",
                             "karim/audit-trailers")
        self.assertIsNotNone(row)
        self.assertEqual(row["trailer"], "gawaher")
        self.assertEqual(row["branch_agent"], "karim")
        self.assertEqual(row["pr"], 33)

    def test_match_clean(self):
        row = db.misattr_row("mokalem", "8c123928" + "0" * 32,
                             "rebase thing (#78)\n\nAgent: kaleem\n",
                             "kaleem/rebase-78")
        self.assertIsNone(row)

    def test_no_trailer_not_misattributed(self):
        row = db.misattr_row("mokalem", "69e58275" + "0" * 32,
                             "healthcheck (#252)\n", "kaleem/251-healthcheck-shared-slot")
        self.assertIsNone(row)

    def test_no_pr_suffix_skipped(self):
        row = db.misattr_row("ins-infra", "4b55488f" + "0" * 32,
                             "docs update\n\nAgent: karim\n", "main")
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
