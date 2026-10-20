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

    def test_v5_in_progress_case_insensitive(self):
        f = flags([issue(updated=NOW - 61 * 60, status="In Progress")])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v5_todo_status_24h_window(self):
        self.assertEqual(flags([issue(updated=NOW - 23 * 3600, status="Todo")]), [])
        f = flags([issue(updated=NOW - 25 * 3600, status="Todo")])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v5_skipped_when_blocked_marked(self):
        self.assertEqual(flags([issue(updated=NOW - 25 * 3600, blocked=[5], blocked_line=True, status="In progress")]), [])

    def test_v6_blocked_line_without_blocker(self):
        f = flags([issue(blocked=(), blocked_line=True)])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v6_blocker_closed(self):
        f = flags([issue(blocked=[5], blocked_line=True, closed=[5])])
        self.assertTrue(any(x["id"] == "ins-infra:1" for x in f))

    def test_v6_cross_repo_open_blocker_clean(self):
        it = issue(blocked=(), blocked_line=True)
        it["blocked_by_cross"] = ["b#5"]
        it["blocked_by_closed"] = []
        self.assertEqual(flags([it]), [])

    def test_v6_cross_repo_closed_blocker_flagged(self):
        it = issue(blocked=(), blocked_line=True)
        it["blocked_by_cross"] = ["b#5"]
        it["blocked_by_closed"] = ["b#5"]
        f = flags([it])
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


REGD = {"labels": {"karim", "kaleem", "dina", "tamer"},
        "aliases": {"salma": "kaleem"},
        "ids": {"agent-01M42Y047WEGCB4V5Z7K128C4T": "karim",      # owns_ids (ELEC clock)
                "agent-01M511VSJ3M80JNSZSZGSY12GM": "tamer"}}    # retired_ids


def issue2(number=1, agent=None, owner_hint=None, last_editor=None,
           last_editor_type=None, last_editor_trailer=None, **kw):
    it = issue(number=number, agent=agent, **kw)
    it["owner_hint"] = owner_hint
    it["last_editor"] = last_editor
    it["last_editor_type"] = last_editor_type
    it["last_editor_trailer"] = last_editor_trailer
    return it


def flagsd(issues, prs=(), rule=RULE, reg=REGD):
    return ck.github_issue_hygiene(rule, {"issues": issues, "prs": list(prs), "registry": reg}, NOW)


class R17OwnerResolutionTest(unittest.TestCase):
    """Dina FINAL 09:22: owner = registered agent: label, else body Owner line
    (name or id in registry, stop at '.' or newline), else V1."""

    def test_body_owner_id_resolves_via_owns_ids(self):
        f = flagsd([issue2(owner_hint="agent-01M42Y047WEGCB4V5Z7K128C4T, todo-01M3X7Z3SXZ6GCHVQ7QQ83T21C")])
        self.assertNotIn("ins-infra:1", [x["id"] for x in f if x["count"] in (1, 2)])

    def test_retired_id_resolves(self):
        f = flagsd([issue2(owner_hint="agent-01M511VSJ3M80JNSZSZGSY12GM.")])
        self.assertNotIn("ins-infra:1", [x["id"] for x in f if x["count"] in (1, 2)])

    def test_alias_label_resolves(self):
        f = flagsd([issue2(agent="salma")])
        self.assertNotIn("ins-infra:1", [x["id"] for x in f if x["count"] == 2])

    def test_unknown_label_still_v2(self):
        f = flagsd([issue2(agent="ghost")])
        self.assertIn(2, [x["count"] for x in f if x["id"] == "ins-infra:1"])

    def test_nothing_resolves_is_v1(self):
        f = flagsd([issue2(owner_hint="nobody-known")])
        self.assertIn(1, [x["count"] for x in f if x["id"] == "ins-infra:1"])

    def test_label_wins_over_body(self):
        f = flagsd([issue2(agent="karim", owner_hint="nobody-known")])
        self.assertNotIn("ins-infra:1", [x["id"] for x in f if x["count"] in (1, 2)])

    def test_body_owner_counts_for_v7(self):
        # karim owns via body; unowned work exists -> idle agents flag, but karim not idle
        f = flagsd([issue2(number=1, owner_hint="agent-01M42Y047WEGCB4V5Z7K128C4T"),
                    issue2(number=2, agent=None, owner_hint=None)])
        self.assertNotIn("karim", [x["id"] for x in f if x["count"] == 7])


class R17V10Test(unittest.TestCase):
    """V10 (Dina): attribution order (a) Co-authored-by/Owner trailer on the
    newest commit reference, (b) agent: label, (c) skip. V10 fires only when
    (a) names a different agent than the owner. Shared-agent logins, bots and
    humans without (a) count as unattributed, never as findings."""

    def test_shared_login_edit_no_v10(self):
        f = flagsd([issue2(agent="karim", last_editor="ins-sherbeeny")])
        self.assertEqual([x for x in f if x["count"] == 10], [])

    def test_bot_edit_no_v10(self):
        f = flagsd([issue2(agent="karim", last_editor="some-bot", last_editor_type="Bot")])
        self.assertEqual([x for x in f if x["count"] == 10], [])

    def test_human_edit_no_v10_unattributed(self):
        data = {"issues": [issue2(agent="karim", last_editor="random-human")], "prs": [], "registry": REGD}
        out = ck.github_issue_hygiene(RULE, data, NOW, baseline=True)
        self.assertEqual(out["counts"]["editor_unattributed"], 1)
        self.assertEqual([x for x in out["flags"] if x["count"] == 10], [])

    def test_trailer_naming_another_agent_report_only(self):
        f = flagsd([issue2(agent="karim", last_editor="ins-sherbeeny", last_editor_trailer="dina")])
        v10 = [x for x in f if x["count"] == 10]
        self.assertEqual(len(v10), 1)
        self.assertEqual(v10[0].get("note"), "report_only")

    def test_trailer_naming_owner_no_flag(self):
        f = flagsd([issue2(agent="karim", last_editor_trailer="karim")])
        self.assertEqual([x for x in f if x["count"] == 10], [])

    def test_trailer_alias_resolves(self):
        f = flagsd([issue2(agent="kaleem", last_editor_trailer="salma")])
        self.assertEqual([x for x in f if x["count"] == 10], [])

    def test_registered_login_edit_is_attributed_no_flag(self):
        reg = dict(REGD)
        reg["logins"] = {"karim-gh": "karim"}
        f = ck.github_issue_hygiene(RULE, {"issues": [issue2(agent="karim", last_editor="karim-gh")],
                                           "prs": [], "registry": reg}, NOW)
        self.assertEqual([x for x in f if x["count"] == 10], [])

    def test_registered_login_for_other_agent_counts_unattributed(self):
        reg = dict(REGD)
        reg["logins"] = {"dina-gh": "dina"}
        data = {"issues": [issue2(agent="karim", last_editor="dina-gh")], "prs": [], "registry": reg}
        out = ck.github_issue_hygiene(RULE, data, NOW, baseline=True)
        self.assertEqual(out["counts"]["editor_unattributed"], 1)
        self.assertEqual([x for x in out["flags"] if x["count"] == 10], [])

    def test_editor_mismatch_without_trailer_no_v10(self):
        f = flagsd([issue2(agent="karim", last_editor="dina")])
        self.assertEqual([x for x in f if x["count"] == 10], [])


class RegistryLoginsTest(unittest.TestCase):
    def test_parse_github_logins_and_shared(self):
        import tempfile
        yaml_text = (
            'shared_logins: [ins-sherbeeny, Ins-Agent]\n'
            'agents:\n'
            '  - {label: karim, name: "Karim", agent_id: agent-01M5BWTFBGC060Y1Q7V34TFCCF, status: active, github_logins: [Karim-GH, karim-alt]}\n')
        p = tempfile.mktemp()
        open(p, "w").write(yaml_text)
        reg = ck.parse_registry(p)
        self.assertEqual(reg["logins"], {"karim-gh": "karim", "karim-alt": "karim"})
        self.assertEqual(reg["shared"], {"ins-sherbeeny", "ins-agent"})


class R17RulesFileTest(unittest.TestCase):
    def test_r29_r30_manual_and_total_32(self):
        import hashlib
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules.yaml")
        sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        rules = ck.load_rules(path, sha)
        out = ck.run(rules, [], now=NOW)
        self.assertEqual(out["total_rules"], 32)
        self.assertIn("R29", out["no_script_check"])
        self.assertIn("R30", out["no_script_check"])
        self.assertIn("R31", out["no_script_check"])
        self.assertIn("R32", out["no_script_check"])


class RegistryParseTest(unittest.TestCase):
    def test_parse_registry_full(self):
        import tempfile
        yaml_text = (
            'agents:\n'
            '  - {label: kaleem, name: "Kaleem (was Salma), calls", agent_id: agent-01M5566SQRSQZKMVJK8BASJC50, status: active, aliases: [salma]}\n'
            '  - {label: tamer, name: "Tamer (retired)", agent_id: agent-01M5B5G0YSMKRK3AB3SF5CWGR5, status: active, retired_ids: [agent-01M511VSJ3M80JNSZSZGSY12GM]}\n'
            '  - {label: karim, name: "Karim (small infra coder, checker v0)", agent_id: agent-01M5BWTFBGC060Y1Q7V34TFCCF, status: active, owns_ids: [agent-01M42Y047WEGCB4V5Z7K128C4T]}\n')
        p = tempfile.mktemp()
        open(p, "w").write(yaml_text)
        reg = ck.parse_registry(p)
        self.assertEqual(reg["labels"], {"kaleem", "tamer", "karim"})
        self.assertEqual(reg["aliases"], {"salma": "kaleem"})
        self.assertEqual(reg["ids"]["agent-01M42Y047WEGCB4V5Z7K128C4T"], "karim")
        self.assertEqual(reg["ids"]["agent-01M511VSJ3M80JNSZSZGSY12GM"], "tamer")
        self.assertEqual(reg["ids"]["agent-01M5566SQRSQZKMVJK8BASJC50"], "kaleem")


class RulesYamlStrictTest(unittest.TestCase):
    def test_rules_yaml_safe_loads(self):
        import pathlib
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        path = pathlib.Path(__file__).resolve().parent.parent / "rules.yaml"
        doc = yaml.safe_load(path.read_text())
        ids = [r["id"] for r in doc["rules"]]
        self.assertEqual(len(ids), 32)
        self.assertIn("R29", ids)
        self.assertIn("R30", ids)
        self.assertIn("R31", ids)
        self.assertIn("R32", ids)


class RegistryFailClosedTest(unittest.TestCase):
    def test_issues_feed_without_registry_is_unproven(self):
        # Samir PR #3: missing registry must degrade R17, never measure.
        import hashlib, json, subprocess, tempfile
        d = tempfile.mkdtemp()
        sha = hashlib.sha256(open("rules.yaml", "rb").read()).hexdigest()
        ep = os.path.join(d, "e.jsonl")
        open(ep, "w").write(json.dumps({"id": "e1", "ts": 1, "author": "user", "topic": "t", "kind": "msg", "text": "hi"}) + "\n")
        mp = os.path.join(d, "m.json")
        open(mp, "w").write(json.dumps({"skipped": {}}))
        ip = os.path.join(d, "i.jsonl")
        open(ip, "w").write(json.dumps(issue()) + "\n")
        r = subprocess.run([sys.executable, "checker.py", "rules.yaml", sha, ep, mp, ip],
                           capture_output=True, text=True)
        out = json.loads(r.stdout)
        self.assertIn("R17", out["unproven_or_failed"])
        self.assertEqual([f for f in out["flags"] if f["rule"] == "R17"], [])

    def test_unparsable_registry_is_unproven(self):
        import hashlib, json, subprocess, tempfile
        d = tempfile.mkdtemp()
        sha = hashlib.sha256(open("rules.yaml", "rb").read()).hexdigest()
        ep = os.path.join(d, "e.jsonl")
        open(ep, "w").write(json.dumps({"id": "e1", "ts": 1, "author": "user", "topic": "t", "kind": "msg", "text": "hi"}) + "\n")
        mp = os.path.join(d, "m.json")
        open(mp, "w").write(json.dumps({"skipped": {}}))
        ip = os.path.join(d, "i.jsonl")
        open(ip, "w").write(json.dumps(issue()) + "\n")
        rp = os.path.join(d, "bad.yaml")
        open(rp, "w").write("{{{ not a registry\n")
        r = subprocess.run([sys.executable, "checker.py", "rules.yaml", sha, ep, mp, ip, rp],
                           capture_output=True, text=True)
        out = json.loads(r.stdout)
        self.assertIn("R17", out["unproven_or_failed"])


class WrapperWiringTest(unittest.TestCase):
    def test_wrapper_runs_snapshot_and_passes_feed(self):
        import pathlib, subprocess
        w = (pathlib.Path(__file__).resolve().parent.parent / "wrapper.sh").read_text()
        self.assertIn("gh_snapshot.py", w)
        self.assertIn("$DIR/issues.jsonl $REG $DIR/ghmeta.json", w)
        self.assertIn('GH_REGISTRY:-$BASE/registry/agents.yaml', w)
        subprocess.run(["sh", "-n", str(pathlib.Path(__file__).resolve().parent.parent / "wrapper.sh")], check=True)


class FeedMetaTest(unittest.TestCase):
    """Amen PR #6: the feed's degraded states must be visible in the output."""

    def run_checker(self, meta):
        import hashlib, json, subprocess, tempfile
        d = tempfile.mkdtemp()
        sha = hashlib.sha256(open("rules.yaml", "rb").read()).hexdigest()
        ep = os.path.join(d, "e.jsonl")
        open(ep, "w").write(json.dumps({"id": "e1", "ts": 1, "author": "user", "topic": "t", "kind": "msg", "text": "hi"}) + "\n")
        mp = os.path.join(d, "m.json")
        open(mp, "w").write(json.dumps({"skipped": {}}))
        ip = os.path.join(d, "i.jsonl")
        open(ip, "w").write(json.dumps(issue()) + "\n")
        rp = os.path.join(d, "r.yaml")
        open(rp, "w").write("- {label: karim, name: \"Karim\", agent_id: agent-01M5BWTFBGC060Y1Q7V34TFCCF, status: active}\n")
        gp = os.path.join(d, "g.json")
        open(gp, "w").write(json.dumps(meta))
        r = subprocess.run([sys.executable, "checker.py", "rules.yaml", sha, ep, mp, ip, rp, gp],
                           capture_output=True, text=True)
        return json.loads(r.stdout)

    def test_feed_meta_visible(self):
        out = self.run_checker({"project": "error", "project_error": "github 403: forbidden",
                                "blocked_native": "rest", "editor_failed": 2, "deps_failed": 0,
                                "repos_failed": [], "project_pages_capped": False})
        feed = out["r17_feed"]
        self.assertEqual(feed["project"], "error")
        self.assertEqual(feed["project_error"], "github 403: forbidden")
        self.assertEqual(feed["editor_failed"], 2)

    def test_feed_meta_unreadable_is_flagged(self):
        import hashlib, json, subprocess, tempfile
        d = tempfile.mkdtemp()
        sha = hashlib.sha256(open("rules.yaml", "rb").read()).hexdigest()
        ep = os.path.join(d, "e.jsonl")
        open(ep, "w").write(json.dumps({"id": "e1", "ts": 1, "author": "user", "topic": "t", "kind": "msg", "text": "hi"}) + "\n")
        mp = os.path.join(d, "m.json")
        open(mp, "w").write(json.dumps({"skipped": {}}))
        ip = os.path.join(d, "i.jsonl")
        open(ip, "w").write(json.dumps(issue()) + "\n")
        rp = os.path.join(d, "r.yaml")
        open(rp, "w").write("- {label: karim, name: \"Karim\", agent_id: agent-01M5BWTFBGC060Y1Q7V34TFCCF, status: active}\n")
        gp = os.path.join(d, "g.json")
        open(gp, "w").write("{ not json")
        r = subprocess.run([sys.executable, "checker.py", "rules.yaml", sha, ep, mp, ip, rp, gp],
                           capture_output=True, text=True)
        out = json.loads(r.stdout)
        self.assertEqual(out["r17_feed"], {"error": "feed meta unreadable"})
