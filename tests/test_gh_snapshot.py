"""Tests for gh_snapshot.py (R17 GitHub issues feed). No network: fake opener."""
import importlib, json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
gs = importlib.import_module("gh_snapshot")

TOKEN = "ghp_unit_test_snapshot"


class FakeHTTP:
    """Serves canned GitHub API pages keyed by (path-without-host)."""
    def __init__(self, pages, fail_paths=()):
        self.pages = pages
        self.fail_paths = fail_paths
        self.urls = []

    class R:
        def __init__(self, payload):
            self._b = json.dumps(payload).encode()
            self.status = 200
            self.headers = {}
        def read(self, *a):
            return self._b
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def __call__(self, req, timeout=0):
        url = req.full_url
        self.urls.append(url)
        path = url.split("api.github.com", 1)[1]
        for fp in self.fail_paths:
            if fp in path:
                raise gs.GhError(500, {"message": "boom"})
        for prefix, payload in self.pages.items():
            if prefix in path:
                return self.R(payload)
        raise gs.GhError(404, {"message": "not found"})


ISSUE = {"number": 5, "title": "t", "state": "open", "pull_request": None,
         "labels": [{"name": "agent:karim"}], "assignees": [{"login": "ins-sherbeeny"}],
         "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-20T08:00:00Z",
         "body": "Due: 2026-10-21\nBlocked by #12\nrest"}


def issue(**kw):
    d = dict(ISSUE)
    d.update(kw)
    if d["pull_request"] is None:
        del d["pull_request"]
    return d


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        import tempfile, stat
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.env = {"GH_TOKEN_FILE": self.tok}

    def test_maps_issues_and_skips_prs(self):
        http = FakeHTTP({"/issues": [issue(), issue(number=6, pull_request={"url": "x"}), issue(number=7, labels=[])]})
        (rows, prs), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual([r["number"] for r in rows], [5, 7])
        a = rows[0]
        self.assertEqual(a["repo"], "r")
        self.assertEqual(a["agent"], "karim")
        self.assertEqual(a["due"], "2026-10-21")
        self.assertEqual(a["blocked_by"], [12])
        self.assertIsInstance(a["updated_ts"], (int, float))
        self.assertEqual(rows[1]["agent"], None)
        self.assertEqual(rows[1]["due"], "2026-10-21")  # same fixture body
        self.assertEqual(rows[1]["blocked_by"], [12])
        self.assertEqual(meta["repos_failed"], [])

    def test_missing_due_and_blockers(self):
        http = FakeHTTP({"/issues": [issue(body="no meta here")]})
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertIsNone(rows[0]["due"])
        self.assertEqual(rows[0]["blocked_by"], [])

    def test_repo_failure_collected_and_nonzero(self):
        http = FakeHTTP({"/issues": [issue()]}, fail_paths=["bad"])
        (rows, prs), meta = gs.snapshot(env=self.env, repos=["ok", "bad"], opener=http)
        self.assertEqual(meta["repos_failed"], ["bad"])

    def test_refuses_world_readable_token(self):
        os.chmod(self.tok, 0o644)
        with self.assertRaises(SystemExit):
            gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP({}))

    def test_due_only_from_first_line(self):
        http = FakeHTTP({"/issues": [issue(body="intro\nDue: 2026-10-22\n")]})
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertIsNone(rows[0]["due"])

    def test_blocked_by_variants(self):
        http = FakeHTTP({"/issues": [issue(body="Due: 2026-10-21\nBlocked by #12, #13 and owner/repo#14") ]})
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(rows[0]["blocked_by"], [12, 13])


if __name__ == "__main__":
    unittest.main()


class OwnerHintTest(unittest.TestCase):
    def test_owner_hint_stops_at_period(self):
        self.assertEqual(gs.parse_owner("Owner: karim. Next steps"), "karim")

    def test_owner_hint_stops_at_newline(self):
        self.assertEqual(gs.parse_owner("Owner: agent-01M42, todo-xyz\nAction: do"), "agent-01M42, todo-xyz")

    def test_owner_hint_case_insensitive(self):
        self.assertEqual(gs.parse_owner("owner: dina"), "dina")

    def test_owner_hint_absent(self):
        self.assertIsNone(gs.parse_owner("no owner here"))

    def test_row_carries_owner_hint(self):
        it = {"number": 5, "state": "open", "labels": [], "assignees": [],
              "body": "Due: 2026-10-21\nOwner: karim.\nrest", "created_at": "2026-10-20T00:00:00Z",
              "updated_at": "2026-10-20T01:00:00Z"}
        self.assertEqual(gs.row_of(it, "r")["owner_hint"], "karim")


def _boom(req, timeout=0):
    raise gs.GhError(401, {"message": "bad credentials"})


class SnapshotSafetyTest(unittest.TestCase):
    def test_no_search_api_in_source(self):
        import pathlib
        src = pathlib.Path(gs.__file__).read_text()
        self.assertNotIn("/search/", src)

    def test_token_never_in_errors(self):
        secret = "ghp_unit_secret_never_print"
        gh = gs.Gh(secret, opener=_boom)
        try:
            gh.get("/repos/x")
        except gs.GhError as e:
            self.assertNotIn(secret, str(e))
            self.assertNotIn(secret, str(e.payload))
        else:
            self.fail("expected GhError")


class ToEpochTest(unittest.TestCase):
    def test_to_epoch_correct_under_dst_tz(self):
        import os, time
        old = os.environ.get("TZ")
        os.environ["TZ"] = "Africa/Cairo"
        time.tzset()
        try:
            self.assertEqual(gs.to_epoch("2026-10-20T00:00:00Z"), 1792454400)
            self.assertEqual(gs.to_epoch("2026-07-01T12:00:00Z"), 1782907200)
        finally:
            if old is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_rows_do_not_emit_last_editor_yet(self):
        # V10 stays silent until the feed captures editor data (documented).
        it = {"number": 5, "state": "open", "labels": [], "assignees": [],
              "body": "Due: 2026-10-21", "created_at": "2026-10-20T00:00:00Z",
              "updated_at": "2026-10-20T01:00:00Z"}
        self.assertNotIn("last_editor", gs.row_of(it, "r"))
