"""Weekly one-command compliance report (issue #34): aggregates the
missing-Due audit rows and trailer-adoption rows from due_backfill.py
into a single JSON report. Pure aggregation, LLM-free, stdlib-only."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import weekly_audit


class AggregateTest(unittest.TestCase):
    MISSING = [
        {"repo": "ins-infra", "number": 13, "owner": "kaleem", "created": "2026-10-01"},
        {"repo": "ins-docs", "number": 20, "owner": "bridge", "created": "2026-10-17"},
    ]
    TRAILERS = [
        {"repo": "ins-infra", "total": 21, "with": 6, "without": 15, "pct": 28},
        {"repo": "mokalem", "total": 11, "with": 3, "without": 8, "pct": 27},
        {"repo": "ins-site", "total": 6, "with": 0, "without": 6, "pct": 0},
    ]

    def test_totals(self):
        rep = weekly_audit.aggregate(self.MISSING, self.TRAILERS, since="2026-10-14")
        self.assertEqual(rep["missing_due"], 2)
        self.assertEqual(rep["trailers"]["total"], 38)
        self.assertEqual(rep["trailers"]["with"], 9)
        self.assertEqual(rep["trailers"]["pct"], 100 * 9 // 38)

    def test_per_repo_rows_preserved(self):
        rep = weekly_audit.aggregate(self.MISSING, self.TRAILERS, since="2026-10-14")
        self.assertEqual(rep["missing_due_rows"], self.MISSING)
        self.assertEqual(rep["trailer_rows"], self.TRAILERS)
        self.assertEqual(rep["since"], "2026-10-14")

    def test_empty(self):
        rep = weekly_audit.aggregate([], [], since="2026-10-14")
        self.assertEqual(rep["missing_due"], 0)
        self.assertEqual(rep["trailers"], {"total": 0, "with": 0, "pct": 0})

    def test_serializes(self):
        rep = weekly_audit.aggregate(self.MISSING, self.TRAILERS, since="2026-10-14")
        json.dumps(rep)  # must be JSON-serializable


if __name__ == "__main__":
    unittest.main()

class MisattrSplitTest(unittest.TestCase):
    def test_misattr_rows_split_out(self):
        rows = [
            {"repo": "a", "total": 4, "with": 2, "without": 2, "pct": 50},
            {"repo": "ins-compliance", "sha": "51e0939d", "pr": 33,
             "trailer": "gawaher", "branch_agent": "karim"},
        ]
        rep = weekly_audit.aggregate([], rows, since="2026-10-15")
        assert rep["trailers"]["total"] == 4
        assert rep["misattributed"] == 1
        assert rep["misattributed_rows"][0]["trailer"] == "gawaher"
        assert len(rep["trailer_rows"]) == 1
