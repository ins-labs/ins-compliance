"""Regression tests: the R17 baseline no_due count must match V3 semantics.

Bug: V3 flags a missing OR unparseable Due ("no usable due"), but the baseline
counts["no_due"] summed only issues with an empty due. A malformed non-empty
Due was flagged V3 while still counting as covered, overstating Due coverage.
"""
import importlib, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ck = importlib.import_module("checker")

NOW = 1_760_000_000  # fixed point in time
RULE = {"id": "R17", "check": "github_issue_hygiene", "threshold": 60, "severity": "medium", "owner": "balancer"}


def issue(repo="ins-infra", number=1, agent="karim", due="2999-01-01", updated=NOW):
    return {"kind": "issue", "repo": repo, "number": number, "agent": agent, "due": due,
            "updated_ts": updated, "blocked_by": [], "blocked_line_present": False,
            "blocked_by_closed": [], "status": None}


def data(issues):
    return {"issues": issues, "prs": [], "registry": set()}


class DueBaselineTest(unittest.TestCase):
    def test_malformed_due_flagged_v3(self):
        # pre-existing behavior, pinned: unparseable non-empty due = no usable due
        out = ck.github_issue_hygiene(RULE, data([issue(due="next Tuesday")]), NOW)
        self.assertTrue(any(x["id"] == "ins-infra:1" and x["count"] == 3 for x in out))

    def test_malformed_due_counts_as_no_due(self):
        out = ck.github_issue_hygiene(RULE, data([issue(due="next Tuesday")]), NOW, baseline=True)
        self.assertEqual(out["counts"]["no_due"], 1)

    def test_valid_due_not_counted(self):
        out = ck.github_issue_hygiene(RULE, data([issue(due="2999-01-01")]), NOW, baseline=True)
        self.assertEqual(out["counts"]["no_due"], 0)

    def test_mixed_set_count_matches_v3_semantics(self):
        issues = [issue(number=1, due="2999-01-01"),  # valid future: usable
                  issue(number=2, due="2000-01-01"),  # valid past: usable (V4, still a usable due)
                  issue(number=3, due=None),          # missing
                  issue(number=4, due=""),            # empty
                  issue(number=5, due="2026-13-40")]  # malformed
        out = ck.github_issue_hygiene(RULE, data(issues), NOW, baseline=True)
        self.assertEqual(out["counts"]["open_issues"], 5)
        self.assertEqual(out["counts"]["no_due"], 3)

    def test_baseline_count_agrees_with_v3_flags(self):
        # consistency: the set flagged V3 in flag mode is exactly the no_due count in baseline
        issues = [issue(number=1, due="garbage"), issue(number=2, due=None), issue(number=3, due="2999-01-01")]
        flagged = ck.github_issue_hygiene(RULE, data(issues), NOW)
        v3 = {x["id"] for x in flagged if x["count"] == 3}
        self.assertEqual(v3, {"ins-infra:1", "ins-infra:2"})
        out = ck.github_issue_hygiene(RULE, data(issues), NOW, baseline=True)
        self.assertEqual(out["counts"]["no_due"], len(v3))


if __name__ == "__main__":
    unittest.main()
