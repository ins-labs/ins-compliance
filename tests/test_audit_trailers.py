"""Red-first tests for --audit-trailers (issue #32)."""
import json
import unittest

from tools.due_backfill import trailer_rows


def commit(msg, sha="abc123"):
    return {"sha": sha, "commit": {"message": msg}}


class TrailerRows(unittest.TestCase):
    def test_counts_trailer_and_plain(self):
        rows = trailer_rows("mokalem", [
            commit("fix: thing (#1) (#2)\n\nAgent: karim\n"),
            commit("other: no trailer (#3) (#4)\n"),
        ])
        self.assertEqual(rows, {"repo": "mokalem", "total": 2, "with": 1, "without": 1, "pct": 50})

    def test_trailer_must_be_on_own_line_at_line_start(self):
        rows = trailer_rows("r", [commit("subject\nsigned: Agent: notreal\n"), commit("x\nAgent: dina")])
        self.assertEqual(rows["with"], 1)
        self.assertEqual(rows["without"], 1)

    def test_empty_repo(self):
        rows = trailer_rows("r", [])
        self.assertEqual(rows, {"repo": "r", "total": 0, "with": 0, "without": 0, "pct": 0})

    def test_suffix_transition_form_counts(self):
        rows = trailer_rows("r", [commit("subject (agent:karim) (#5)")])
        self.assertEqual(rows["with"], 1)


if __name__ == "__main__":
    unittest.main()
