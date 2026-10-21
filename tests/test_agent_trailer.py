"""Tests for the Agent: <label> commit trailer (ins-infra #496, ADR-0003, V11)."""
import importlib, json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
gs = importlib.import_module("gh_snapshot")
ck = importlib.import_module("checker")

NOW = 1_760_000_000
RULE = {"id": "R17", "check": "github_issue_hygiene", "threshold": 60, "severity": "medium", "owner": "balancer"}


class ParseAttributionTest(unittest.TestCase):
    def test_trailer(self):
        self.assertEqual(gs.parse_commit_attribution("fix thing\n\nAgent: karim"), ("karim", "trailer"))

    def test_trailer_after_other_trailers(self):
        msg = "fix thing\n\nReviewed-by: samir\nAgent: dina\n"
        self.assertEqual(gs.parse_commit_attribution(msg), ("dina", "trailer"))

    def test_suffix_transition(self):
        self.assertEqual(gs.parse_commit_attribution("fix thing (agent:karim)"), ("karim", "suffix"))

    def test_trailer_wins_over_suffix(self):
        msg = "fix thing (agent:old)\n\nAgent: karim"
        self.assertEqual(gs.parse_commit_attribution(msg), ("karim", "trailer"))

    def test_none(self):
        self.assertEqual(gs.parse_commit_attribution("plain subject"), (None, None))

    def test_trailer_must_be_own_line(self):
        self.assertEqual(gs.parse_commit_attribution("see Agent: karim notes"), (None, None))


COMMIT = {"sha": "abc123def456", "author": {"login": "ins-sherbeeny"},
          "commit": {"message": "x", "committer": {"date": "2026-10-21T10:00:00Z"},
                     "author": {"name": "n"}}}


def commit(sha="abc123def456", login="ins-sherbeeny", message="x", date="2026-10-21T10:00:00Z"):
    c = json.loads(json.dumps(COMMIT))
    c["sha"] = sha
    c["author"]["login"] = login
    c["commit"]["message"] = message
    c["commit"]["committer"]["date"] = date
    return c


class SnapshotCommitsTest(unittest.TestCase):
    def setUp(self):
        import tempfile, stat
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        open(self.tok, "w").write("ghp_unit")
        os.chmod(self.tok, 0o600)
        self.state = os.path.join(self.tmp, "state.json")
        self.env = {"GH_TOKEN_FILE": self.tok, "GH_SNAPSHOT_STATE": self.state}

    def http(self, commits):
        from test_gh_snapshot import FakeHTTP, issue
        return FakeHTTP({"/issues?state=open": [issue()], "/commits": commits})

    def test_commit_rows_emitted_with_attribution(self):
        http = self.http([commit(message="fix\n\nAgent: karim")])
        (rows, _, commits), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]["agent"], "karim")
        self.assertEqual(commits[0]["attr_form"], "trailer")
        self.assertEqual(commits[0]["author"], "ins-sherbeeny")
        self.assertEqual(commits[0]["repo"], "r")

    def test_seen_shas_not_repeated(self):
        http = self.http([commit(sha="aaa111", message="fix\n\nAgent: karim")])
        gs.snapshot(env=self.env, repos=["r"], opener=http)
        (_, _, commits2), _ = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(commits2, [])

    def test_commit_fetch_failure_does_not_fail_repo(self):
        from test_gh_snapshot import FakeHTTP, issue
        http = FakeHTTP({"/issues?state=open": [issue()]}, fail_paths=("/commits",))
        (rows, _, commits), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(meta["repos_failed"], [])
        self.assertEqual(meta.get("commits_failed"), ["r"])
        self.assertEqual(commits, [])


REGD = {"labels": {"karim", "dina"}, "aliases": {}, "ids": {},
        "logins": {}, "shared": {"ins-sherbeeny"}}


def crow(sha="abc123def456", author="ins-sherbeeny", agent=None):
    return {"kind": "commit", "repo": "ins-infra", "sha": sha, "ts": NOW,
            "author": author, "agent": agent, "attr_form": "trailer" if agent else None}


def cflags(commits, reg=REGD):
    feed = {"issues": [], "prs": [], "commits": commits, "registry": reg}
    return ck.github_issue_hygiene(RULE, feed, NOW)


class V11Test(unittest.TestCase):
    def test_shared_login_no_attribution_flagged_report_only(self):
        f = cflags([crow()])
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["id"], "ins-infra:abc123de")
        self.assertEqual(f[0].get("note"), "report_only")

    def test_valid_trailer_clean(self):
        self.assertEqual(cflags([crow(agent="karim")]), [])

    def test_alias_label_resolves(self):
        reg = dict(REGD); reg["aliases"] = {"k": "karim"}
        self.assertEqual(cflags([crow(agent="k")], reg=reg), [])

    def test_unregistered_label_flagged(self):
        f = cflags([crow(agent="ghost")])
        self.assertEqual(len(f), 1)

    def test_human_author_skipped(self):
        self.assertEqual(cflags([crow(author="some-human")]), [])

    def test_bot_author_skipped(self):
        self.assertEqual(cflags([crow(author="github-actions[bot]")]), [])


if __name__ == "__main__":
    unittest.main()
