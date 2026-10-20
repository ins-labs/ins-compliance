"""Tests for the R17 check github_issue_hygiene (Dina's R17_rewrite_v1, label model)."""
import importlib, json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ck = importlib.import_module("checker")

NOW = 1_760_000_000  # fixed point in time
RULE = {"id": "R17", "check": "github_issue_hygiene", "threshold": 60, "severity": "medium", "owner": "balancer"}
REG = {"karim", "dina", "wazen"}


def issue(repo="ins-infra", number=1, agent="karim", due="2999-01-01", updated=NOW,
          blocked=(), blocked_line=False, closed=(), status=None):
    return {"kind": "issue", "repo": repo, "number": number, "agent": agent, "due": due,
            "updated_ts": updated, "blocked_by": list(blocked), "blocked_line_present": blocked_line,
            "blocked_by_closed": list(closed), "status": status}


def flags(issues, prs=(), rule=RULE, reg=()):
    return ck.github_issue_hygiene(rule, {"issues": issues, "prs": list(prs), "registry": set(reg)}, NOW)


class R17Test(unittest.TestCase):
    def test_clean_issue_no_flags(self):
        self.assertEqual(flags([issue()]), [])

    def test_v1_no_agent_label(self):
        f = flags([issue(agent=None)])
        self.assertEqual([x["id"] for x in f], ["ins-infra:1"])

    def test_v2_unregistered_agent_label(self):
        f = flags([issue(agent="ghost")], reg=REG)
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v3_no_due(self):
        f = flags([issue(due=None)])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v4_past_due(self):
        f = flags([issue(due="2000-01-01")])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v5_in_progress_silent_over_threshold(self):
        f = flags([issue(updated=NOW - 61 * 60, status="In progress")])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v5_todo_needs_24h(self):
        self.assertEqual(flags([issue(updated=NOW - 23 * 3600)]), [])
        f = flags([issue(updated=NOW - 25 * 3600)])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v5_skipped_when_blocked_marked(self):
        self.assertEqual(flags([issue(updated=NOW - 25 * 3600, blocked=[5], blocked_line=True, status="In progress")]), [])

    def test_v6_blocked_line_without_blocker(self):
        f = flags([issue(blocked=(), blocked_line=True)])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v6_blocker_closed(self):
        f = flags([issue(blocked=[5], blocked_line=True, closed=[5])])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v7_idle_agent_with_unassigned_work(self):
        f = flags([issue(agent=None, number=9)], reg=REG)  # unassigned exists; karim/dina/wazen have zero
        self.assertTrue(any(x["id"] == "karim" for x in f))

    def test_v7_not_when_agent_has_issues(self):
        f = flags([issue(), issue(agent=None, number=9)], reg=REG)
        self.assertFalse(any(x["id"] == "karim" for x in f))

    def test_v8_all_issues_silent(self):
        f = flags([issue(updated=NOW - 61 * 60), issue(number=2, updated=NOW - 2 * 3600)], reg=REG)
        self.assertTrue(any(x["id"] == "karim" for x in f))

    def test_v8_not_when_one_issue_fresh(self):
        f = flags([issue(updated=NOW - 61 * 60), issue(number=2, updated=NOW)], reg=REG)
        self.assertFalse(any(x["id"] == "karim" for x in f))

    def test_v9_pr_without_issue_ref(self):
        f = flags([issue()], prs=[{"kind": "pr", "repo": "ins-infra", "number": 55, "has_issue_ref": False}])
        self.assertTrue(any(x["id"] == "ins-infra:55" for x in f))

    def test_v9_pr_with_issue_ref_clean(self):
        f = flags([issue()], prs=[{"kind": "pr", "repo": "ins-infra", "number": 55, "has_issue_ref": True}])
        self.assertEqual(f, [])

    def test_flags_carry_fixed_fields_only(self):
        for x in flags([issue(agent=None, due=None)], prs=[{"kind": "pr", "repo": "r", "number": 1, "has_issue_ref": False}]):
            self.assertEqual(set(x), {"rule", "owner", "id", "age_min", "count"})
            self.assertEqual(x["rule"], "R17")
            self.assertEqual(x["owner"], "balancer")

    def test_baseline_mode_counts_only(self):
        data = {"issues": [issue(agent=None, due=None), issue(number=2)], "prs": [], "registry": REG}
        out = ck.github_issue_hygiene(RULE, data, NOW, baseline=True)
        self.assertEqual(out["flags"], [])
        self.assertEqual(out["counts"]["open_issues"], 2)
        self.assertEqual(out["counts"]["no_agent_label"], 1)
        self.assertEqual(out["counts"]["no_due"], 1)


if __name__ == "__main__":
    unittest.main()
