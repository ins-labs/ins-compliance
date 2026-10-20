"""Tests for gh_snapshot.py (R17 GitHub issues feed). No network: fake opener."""
import importlib, json, os, re, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
gs = importlib.import_module("gh_snapshot")

TOKEN = "ghp_unit_test_snapshot"


class FakeHTTP:
    """Serves canned GitHub API pages keyed by (path-without-host)."""
    def __init__(self, pages, fail_paths=()):
        self.pages = pages
        self.fail_paths = dict.fromkeys(fail_paths, 500) if not isinstance(fail_paths, dict) else fail_paths
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
        for fp, status in self.fail_paths.items():
            if fp in path:
                raise gs.GhError(status, {"message": "boom"})
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
        http = FakeHTTP({"/events": [], "/comments": [], "/dependencies": [],
                    "/issues": [issue(), issue(number=6, pull_request={"url": "x"}), issue(number=7, labels=[])]})
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
        http = FakeHTTP({"/events": [], "/comments": [], "/dependencies": [], "/issues": [issue(body="no meta here")]})
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
        http = FakeHTTP({"/events": [], "/comments": [], "/dependencies": [], "/issues": [issue(body="intro\nDue: 2026-10-22\n")]})
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertIsNone(rows[0]["due"])

    def test_blocked_by_variants(self):
        http = FakeHTTP({"/events": [], "/comments": [], "/dependencies": [], "/issues": [issue(body="Due: 2026-10-21\nBlocked by #12, #13 and owner/repo#14") ]})
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

    def test_row_defaults_last_editor_none(self):
        # row_of is pure mapping; the snapshot pass fills last_editor.
        it = {"number": 5, "state": "open", "labels": [], "assignees": [],
              "body": "Due: 2026-10-21", "created_at": "2026-10-20T00:00:00Z",
              "updated_at": "2026-10-20T01:00:00Z"}
        self.assertIsNone(gs.row_of(it, "r")["last_editor"])


class RedirectSafetyTest(unittest.TestCase):
    """Samir follow-up: the Authorization header must not leak to another
    host on a redirect (urllib forwards request headers by default)."""

    def handler(self):
        return gs.AuthSafeRedirectHandler()

    def req(self):
        import urllib.request
        r = urllib.request.Request("https://api.github.com/repos/x")
        r.add_header("Authorization", "Bearer secret-token")
        return r

    def test_cross_host_redirect_strips_auth(self):
        import io
        new = self.handler().redirect_request(self.req(), io.BytesIO(), 302, "Found", {},
                                              "https://evil.example/hook")
        self.assertNotIn("Authorization", new.headers)

    def test_same_host_redirect_keeps_auth(self):
        import io
        new = self.handler().redirect_request(self.req(), io.BytesIO(), 302, "Found", {},
                                              "https://api.github.com/repositories/1")
        self.assertEqual(new.headers.get("Authorization"), "Bearer secret-token")

    def test_scheme_change_redirect_strips_auth(self):
        # issue #9: origin compare (scheme+host+port), not hostname only -
        # an https->http downgrade to the same host must not carry the token.
        import io
        new = self.handler().redirect_request(self.req(), io.BytesIO(), 302, "Found", {},
                                              "http://api.github.com/repos/x")
        self.assertNotIn("Authorization", new.headers)

    def test_port_change_redirect_strips_auth(self):
        import io
        new = self.handler().redirect_request(self.req(), io.BytesIO(), 302, "Found", {},
                                              "https://api.github.com:8443/repos/x")
        self.assertNotIn("Authorization", new.headers)


class TokenOwnerTest(unittest.TestCase):
    """Samir follow-up: the token file must be owned by root or the caller."""

    def test_refuses_foreign_owned_token_file(self):
        import os, stat, tempfile
        from unittest import mock
        tmp = tempfile.mkdtemp()
        tok = os.path.join(tmp, "tok")
        open(tok, "w").write("x")
        os.chmod(tok, 0o600)
        real = os.stat(tok)
        fake = os.stat_result((real.st_mode, real.st_ino, real.st_dev, real.st_nlink,
                               os.geteuid() + 1000, real.st_gid, real.st_size,
                               real.st_atime, real.st_mtime, real.st_ctime))
        with mock.patch("os.stat", return_value=fake):
            with self.assertRaises(SystemExit):
                gs.read_token({"GH_TOKEN_FILE": tok})


class LastEditorTest(unittest.TestCase):
    """V10 feed: last editor of an issue = actor of the newest issue event or
    comment; falls back to the issue author; per-issue failures are tolerated."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.env = {"GH_TOKEN_FILE": self.tok}

    def test_editor_from_newest_comment(self):
        pages = {
            "/issues/5/events": [{"created_at": "2026-10-19T10:00:00Z", "event": "labeled",
                                   "actor": {"login": "ins-dina"}}],
            "/issues/5/comments": [{"created_at": "2026-10-20T09:00:00Z",
                                     "user": {"login": "ins-wazen"}}],
            "/issues/5/dependencies": [],
            "/issues": [issue()],
        }
        http = FakeHTTP(pages)
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(rows[0]["last_editor"], "ins-wazen")
        self.assertEqual(meta.get("editor_failed", 0), 0)

    def test_editor_from_newest_event(self):
        pages = {
            "/issues/5/events": [{"created_at": "2026-10-20T10:00:00Z", "event": "assigned",
                                   "actor": {"login": "ins-dina"}}],
            "/issues/5/comments": [{"created_at": "2026-10-19T09:00:00Z",
                                     "user": {"login": "ins-wazen"}}],
            "/issues/5/dependencies": [],
            "/issues": [issue()],
        }
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(pages))
        self.assertEqual(rows[0]["last_editor"], "ins-dina")

    def test_editor_falls_back_to_author(self):
        pages = {
            "/issues/5/events": [],
            "/issues/5/comments": [],
            "/issues/5/dependencies": [],
            "/issues": [issue(user={"login": "ins-sherbeeny"})],
        }
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(pages))
        self.assertEqual(rows[0]["last_editor"], "ins-sherbeeny")

    def test_editor_failure_tolerated(self):
        pages = {"/issues/5/dependencies": [], "/issues": [issue()]}
        http = FakeHTTP(pages, fail_paths=["/events", "/comments"])
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertIsNone(rows[0]["last_editor"])
        self.assertEqual(meta["editor_failed"], 1)


class NativeBlockedByTest(unittest.TestCase):
    """Blocked-by links: native issue dependencies via REST when the project
    token is absent. Merged into blocked_by; closed states resolve directly."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.env = {"GH_TOKEN_FILE": self.tok}

    def test_native_links_merge_and_close(self):
        pages = {
            "/issues/5/dependencies/blocked_by": [
                {"number": 12, "state": "open"}, {"number": 44, "state": "open"}],
            "/issues/44": {"number": 44, "state": "closed"},
            "/issues/5/events": [], "/issues/5/comments": [],
            "/issues": [issue(body="Due: 2026-10-21")],
        }
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(pages))
        r = rows[0]
        self.assertEqual(r["blocked_by"], [12, 44])
        # 44 closed per the fresh per-issue GET (link-listing states are never trusted)
        self.assertEqual(r["blocked_by_closed"], [44])
        self.assertTrue(r["blocked_line_present"])
        self.assertEqual(meta.get("blocked_native"), "rest")

    def test_native_404_trips_circuit_break(self):
        pages = {"/issues/5/events": [], "/issues/5/comments": [],
                 "/issues/6/events": [], "/issues/6/comments": [],
                 "/issues": [issue(number=5), issue(number=6)]}
        http = FakeHTTP(pages, fail_paths={"/dependencies": 404})
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(meta.get("blocked_native"), "unsupported")
        self.assertEqual(len([u for u in http.urls if "/dependencies/" in u]), 1)

    def test_transient_5xx_does_not_trip_breaker(self):
        pages = {"/issues/5/events": [], "/issues/5/comments": [],
                 "/issues/6/events": [], "/issues/6/comments": [],
                 "/issues/6/dependencies/blocked_by": [{"number": 9, "state": "open"}],
                 "/issues": [issue(number=5), issue(number=6)]}
        http = FakeHTTP(pages, fail_paths={"/issues/5/dependencies": 500})
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(meta.get("blocked_native"), "rest")
        self.assertEqual(meta.get("deps_failed"), 1)
        self.assertEqual(rows[1]["blocked_by"], [9, 12])  # issue 6 still probed (body ref 12 + native 9)

    def test_body_and_native_union(self):
        pages = {
            "/issues/5/dependencies/blocked_by": [{"number": 13, "state": "open"}],
            "/issues/5/events": [], "/issues/5/comments": [],
            "/issues/12": {"number": 12, "state": "closed"},
            "/issues": [issue(body="Due: 2026-10-21\nBlocked by #12")],
        }
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(pages))
        r = rows[0]
        self.assertEqual(r["blocked_by"], [12, 13])
        self.assertEqual(r["blocked_by_closed"], [12])


PROJECT_ITEM = {"content": {"number": 5, "repository": {"name": "r"}},
                "fieldValues": {"nodes": [
                    {"name": "In progress", "field": {"name": "Status"}},
                    {"date": "2026-10-22", "field": {"name": "Due"}}]}}


def project_pages(item=None, project_title="ins-work"):
    """Fake GraphQL responses: first call finds the project, second lists items."""
    item = PROJECT_ITEM if item is None else item
    return [("organization", {"data": {"organization": {"projectsV2": {"nodes": [
                {"id": "P1", "title": project_title}]}}}}),
            ("items", {"data": {"node": {"items": {
                "nodes": [item], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}})]


class ProjectFieldsTest(unittest.TestCase):
    """ins-work project fields (Status/Due/Blocked by) merge into rows when the
    read:project token exists; without it every project field stays None."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.ptok = os.path.join(self.tmp, "ptok")
        with open(self.ptok, "w") as fh:
            fh.write(TOKEN + "_project")
        os.chmod(self.ptok, 0o600)
        self.env = {"GH_TOKEN_FILE": self.tok, "GH_PROJECT_TOKEN_FILE": self.ptok}

    def http_with_project(self, gql_pages):
        base = {"/issues/5/events": [], "/issues/5/comments": [],
                "/issues/5/dependencies/blocked_by": [{"number": 31, "state": "open"},
                                                      {"number": 32, "state": "open"}],
                "/issues/32": {"number": 32, "state": "closed"},
                "/issues": [issue(body="no due here")]}

        class GqlHTTP(FakeHTTP):
            def __call__(self, req, timeout=0):
                if req.full_url.endswith("/graphql"):
                    body = json.loads(req.data or b"{}")
                    q = body.get("query", "")
                    for marker, payload in gql_pages:
                        if marker in q:
                            return self.R(payload)
                    raise gs.GhError(500, {"message": "bad graphql"})
                return super().__call__(req, timeout=timeout)
        return GqlHTTP(base)

    def test_project_fields_merge(self):
        http = self.http_with_project(project_pages())
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        r = rows[0]
        self.assertEqual(r["status"], "In progress")
        self.assertEqual(r["due"], "2026-10-22")       # project Due fills the gap
        self.assertEqual(r["due_source"], "project")
        self.assertEqual(r["blocked_by"], [31, 32])   # from REST links, not the project
        self.assertEqual(r["blocked_by_closed"], [32])
        self.assertTrue(r["blocked_line_present"])
        self.assertEqual(meta.get("project"), "ok")
        self.assertEqual(meta.get("blocked_native"), "rest")

    def test_body_due_wins_over_project(self):
        http = self.http_with_project(project_pages())
        base_issue = issue()  # has body Due: 2026-10-21
        http.pages["/issues"] = [base_issue]
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(rows[0]["due"], "2026-10-21")
        self.assertEqual(rows[0]["due_source"], "body")

    def test_no_project_token_skips(self):
        env = {"GH_TOKEN_FILE": self.tok}
        pages = {"/issues/5/dependencies/blocked_by": [],
                 "/issues/5/events": [], "/issues/5/comments": [],
                 "/issues": [issue(body="no due here")]}
        (rows, _), meta = gs.snapshot(env=env, repos=["r"], opener=FakeHTTP(pages))
        r = rows[0]
        self.assertIsNone(r["status"])
        self.assertIsNone(r["due"])
        self.assertEqual(meta.get("project"), "no_token")

    def test_project_error_keeps_class_status_no_body(self):
        # issue #7: meta keeps the error class/status only - no response body.
        http = self.http_with_project([])  # every graphql call fails
        (_, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(meta.get("project"), "error")
        self.assertEqual(meta.get("project_error"), "GhError status=500")
        self.assertNotIn("bad graphql", meta.get("project_error", ""))

    def test_project_not_found(self):
        http = self.http_with_project(project_pages(project_title="something-else"))
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(meta.get("project"), "not_found")
        self.assertIsNone(rows[0]["status"])


class GraphqlSchemaTest(unittest.TestCase):
    """Samir PR #6: both query strings must reference only types/fields that
    exist in the pinned schema excerpt (the IssueValue bug class)."""

    SCHEMA = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "graphql_schema_pinned.json")))

    def check(self, query):
        q = re.sub(r"\([^()]*\)", "", query)  # strip argument lists
        types = set(re.findall(r"\.\.\. on (\w+)", q))
        self.assertNotIn("ProjectV2ItemFieldIssueValue", types)
        for t in types:
            self.assertIn(t, self.SCHEMA["types"], "unknown type %s" % t)
        words = set(re.findall(r"[A-Za-z]\w+", q))
        words -= types
        words -= {"query", "on"}                       # keywords
        words -= {"org", "id", "after"}                # variable names
        words -= {"String", "ID"}                      # variable types
        for w in words:
            self.assertIn(w, self.SCHEMA["fields"], "unknown field %s" % w)

    def test_find_query_against_pinned_schema(self):
        self.check(gs.PROJECT_FIND_Q)

    def test_items_query_against_pinned_schema(self):
        self.check(gs.PROJECT_ITEMS_Q)


class SnapshotCacheTest(unittest.TestCase):
    """Samir PR #6: editor/dependency calls are cached by issue updated_ts;
    only issues changed since the last snapshot cost API calls."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.state = os.path.join(self.tmp, "state.json")
        self.env = {"GH_TOKEN_FILE": self.tok, "GH_SNAPSHOT_STATE": self.state}

    def pages(self):
        return {"/issues/5/events": [{"created_at": "2026-10-20T10:00:00Z", "event": "labeled",
                                       "actor": {"login": "ins-dina"}}],
                "/issues/5/comments": [],
                "/issues/5/dependencies/blocked_by": [{"number": 31, "state": "open"}],
                "/issues": [issue()]}

    def test_first_run_fetches_and_writes_cache(self):
        http = FakeHTTP(self.pages())
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(meta["editor_cached"], 0)
        self.assertEqual(meta["deps_cached"], 0)
        cache = json.load(open(self.state))
        self.assertEqual(cache["r:5"]["last_editor"], "ins-dina")
        self.assertEqual(cache["r:5"]["deps"]["same"], [31])  # numbers only; states re-read each run
        self.assertTrue(cache["r:5"]["deps"]["ts"])
        import stat
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o600)

    def test_second_run_uses_cache(self):
        gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(self.pages()))
        http2 = FakeHTTP(self.pages())
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http2)
        self.assertEqual(meta["editor_cached"], 1)
        self.assertEqual(meta["deps_cached"], 1)
        self.assertEqual([u for u in http2.urls if "/events" in u or "/dependencies/" in u], [])
        self.assertEqual(rows[0]["last_editor"], "ins-dina")
        self.assertEqual(rows[0]["blocked_by"], [12, 31])

    def test_changed_issue_refetches(self):
        gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(self.pages()))
        p = self.pages()
        p["/issues"] = [issue(updated_at="2026-10-20T11:00:00Z")]
        http2 = FakeHTTP(p)
        (_, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http2)
        self.assertEqual(meta["editor_cached"], 0)
        self.assertEqual(meta["deps_cached"], 0)

    def test_missing_state_file_is_cold_cache(self):
        env = {"GH_TOKEN_FILE": self.tok, "GH_SNAPSHOT_STATE": os.path.join(self.tmp, "nope.json")}
        (_, _), meta = gs.snapshot(env=env, repos=["r"], opener=FakeHTTP(self.pages()))
        self.assertEqual(meta["editor_cached"], 0)


class TrailerTest(unittest.TestCase):
    """V10 attribution (Dina): a newest commit-reference event yields the
    Co-authored-by/Owner trailer from that commit's message."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.env = {"GH_TOKEN_FILE": self.tok}

    def test_trailer_from_newest_commit_reference(self):
        pages = {"/issues/5/events": [{"created_at": "2026-10-20T10:00:00Z", "event": "referenced",
                                        "actor": {"login": "ins-sherbeeny"}, "commit_id": "abc123"}],
                 "/issues/5/comments": [],
                 "/issues/5/dependencies": [],
                 "/commits/abc123": {"commit": {"message": "Fix thing\n\nCo-authored-by: dina <d@x>"}},
                 "/issues": [issue()]}
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(pages))
        self.assertEqual(rows[0]["last_editor"], "ins-sherbeeny")
        self.assertEqual(rows[0]["last_editor_trailer"], "dina")

    def test_owner_trailer_variant(self):
        pages = {"/issues/5/events": [{"created_at": "2026-10-20T10:00:00Z", "event": "closed",
                                        "actor": {"login": "ins-sherbeeny"}, "commit_id": "abc123"}],
                 "/issues/5/comments": [],
                 "/issues/5/dependencies": [],
                 "/commits/abc123": {"commit": {"message": "Fix thing\nOwner: wazen"}},
                 "/issues": [issue()]}
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(pages))
        self.assertEqual(rows[0]["last_editor_trailer"], "wazen")

    def test_no_trailer_when_newest_is_comment(self):
        pages = {"/issues/5/events": [{"created_at": "2026-10-19T10:00:00Z", "event": "referenced",
                                        "actor": {"login": "ins-sherbeeny"}, "commit_id": "abc123"}],
                 "/issues/5/comments": [{"created_at": "2026-10-20T09:00:00Z",
                                          "user": {"login": "ins-wazen"}}],
                 "/issues/5/dependencies": [],
                 "/issues": [issue()]}
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(pages))
        self.assertEqual(rows[0]["last_editor"], "ins-wazen")
        self.assertIsNone(rows[0]["last_editor_trailer"])


class ProjectPageCapTest(unittest.TestCase):
    """Samir PR #6: the project items walk has a page cap like list_paginated."""

    def test_page_cap_stops_runaway_cursor(self):
        calls = []

        class PG:
            def graphql(self, query, variables=None):
                calls.append(1)
                if "organization" in query:
                    return {"data": {"organization": {"projectsV2": {"nodes": [
                        {"id": "P1", "title": "ins-work"}]}}}}
                return {"data": {"node": {"items": {"nodes": [],
                    "pageInfo": {"hasNextPage": True, "endCursor": "c"}}}}}
        out, capped = gs.project_fields(PG(), max_pages=3)
        self.assertTrue(capped)
        self.assertEqual(len(calls), 4)  # 1 find + 3 capped pages


class CachedBlockerStateTest(unittest.TestCase):
    """Amen PR #6: a closing blocker may not bump the blocked issue's
    updated_ts. Cached deps are numbers only; states are re-read every run."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.state = os.path.join(self.tmp, "state.json")
        self.env = {"GH_TOKEN_FILE": self.tok, "GH_SNAPSHOT_STATE": self.state}

    def test_cached_deps_open_blocker_now_closed(self):
        base = {"/issues/5/events": [], "/issues/5/comments": [],
                "/issues/5/dependencies/blocked_by": [{"number": 31, "state": "open"}],
                "/issues/31": {"number": 31, "state": "open"},
                "/issues": [issue(body="Due: 2026-10-21")]}
        (rows, _), _ = gs.snapshot(env=self.env, repos=["r"], opener=FakeHTTP(dict(base)))
        self.assertEqual(rows[0]["blocked_by_closed"], [])
        # blocker closes without the blocked issue changing
        second = dict(base)
        second["/issues/31"] = {"number": 31, "state": "closed"}
        http2 = FakeHTTP(second)
        (rows, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http2)
        self.assertEqual(meta["deps_cached"], 1)          # cache hit, no deps call
        self.assertEqual([u for u in http2.urls if "/dependencies/" in u], [])
        self.assertEqual(rows[0]["blocked_by_closed"], [31])


class CrossRepoBlockedByTest(unittest.TestCase):
    """Samir PR #6: a cross-repo dependency must never be looked up as a
    same-repo issue. Repro: a#9 blocked by b#5 (open) while a#5 is closed -
    merging the foreign 5 same-repo yields a false V6."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.env = {"GH_TOKEN_FILE": self.tok}

    def pages(self, cross_state):
        return {
            "/repos/org/a/issues/9/dependencies/blocked_by": [
                {"number": 5, "state": "open",
                 "repository_url": "https://api.github.com/repos/org/b"}],
            "/repos/org/a/issues/9/events": [], "/repos/org/a/issues/9/comments": [],
            "/repos/org/a/issues/5": {"number": 5, "state": "closed"},
            "/repos/org/b/issues/5": {"number": 5, "state": cross_state},
            "/repos/org/a/issues": [issue(number=9, body="Due: 2026-10-21")],
        }

    def test_cross_repo_open_blocker_no_false_v6(self):
        (rows, _), _ = gs.snapshot(env=self.env, repos=["a"], org="org",
                                   opener=FakeHTTP(self.pages("open")))
        r = rows[0]
        self.assertEqual(r["blocked_by"], [])               # foreign number stays out
        self.assertEqual(r["blocked_by_cross"], ["b#5"])
        self.assertTrue(r["blocked_line_present"])
        self.assertEqual(r["blocked_by_closed"], [])        # b#5 is OPEN in repo b

    def test_cross_repo_closed_blocker_flagged_qualified(self):
        (rows, _), _ = gs.snapshot(env=self.env, repos=["a"], org="org",
                                   opener=FakeHTTP(self.pages("closed")))
        r = rows[0]
        self.assertEqual(r["blocked_by"], [])
        self.assertEqual(r["blocked_by_closed"], ["b#5"])   # qualified id, right repo

    def test_other_org_dependency_qualified(self):
        pages = self.pages("open")
        pages["/repos/org/a/issues/9/dependencies/blocked_by"] = [
            {"number": 7, "state": "open",
             "repository_url": "https://api.github.com/repos/other/c"}]
        pages["/repos/other/c/issues/7"] = {"number": 7, "state": "closed"}
        (rows, _), _ = gs.snapshot(env=self.env, repos=["a"], org="org",
                                   opener=FakeHTTP(pages))
        r = rows[0]
        self.assertEqual(r["blocked_by_cross"], ["other/c#7"])
        self.assertEqual(r["blocked_by_closed"], ["other/c#7"])


class DepsCacheAgeTest(unittest.TestCase):
    """Samir PR #6: cached deps refresh when older than 1 h."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.tok = os.path.join(self.tmp, "tok")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN)
        os.chmod(self.tok, 0o600)
        self.state = os.path.join(self.tmp, "state.json")
        self.env = {"GH_TOKEN_FILE": self.tok, "GH_SNAPSHOT_STATE": self.state}

    def seed(self, age_s):
        import time as _t
        row_ts = gs.to_epoch("2026-10-20T08:00:00Z")
        json.dump({"r:5": {"updated_ts": row_ts, "last_editor": "ins-dina",
                           "deps": {"same": [31], "cross": [], "ts": int(_t.time()) - age_s}}},
                  open(self.state, "w"))

    def pages(self):
        return {"/issues/5/events": [], "/issues/5/comments": [],
                "/issues/5/dependencies/blocked_by": [{"number": 31, "state": "open"}],
                "/issues": [issue(body="Due: 2026-10-21")]}

    def test_fresh_cache_hit(self):
        self.seed(1800)
        http = FakeHTTP(self.pages())
        (_, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(meta["deps_cached"], 1)
        self.assertEqual([u for u in http.urls if "/dependencies/" in u], [])

    def test_stale_cache_refetches(self):
        self.seed(3700)
        http = FakeHTTP(self.pages())
        (_, _), meta = gs.snapshot(env=self.env, repos=["r"], opener=http)
        self.assertEqual(meta["deps_cached"], 0)
        self.assertEqual(len([u for u in http.urls if "/dependencies/" in u]), 1)
