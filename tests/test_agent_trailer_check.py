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


class LabelsFileTest(unittest.TestCase):
    def _write(self, text):
        import tempfile
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_registry_yaml_flow_style(self):
        import argparse
        path = self._write('agents:\n  - {label: nader, name: "Nader"}\n  - {label: karim, name: "Karim"}\n')
        args = argparse.Namespace(labels=None, labels_file=path)
        self.assertEqual(atc._load_labels(args), ["karim", "nader"])

    def test_agent_prefixed_labels_normalized(self):
        import argparse
        path = self._write('["agent:karim", "agent:dina"]')
        args = argparse.Namespace(labels=None, labels_file=path)
        labels = atc._load_labels(args)
        self.assertIn("karim", labels)
        self.assertNotIn("agent:karim", labels)

    def test_branch_matches_stripped_label(self):
        self.assertEqual(atc.label_from_branch("karim/work", ["agent:karim"]), "karim")


class RepoDefaultLabelsTest(unittest.TestCase):
    def test_newline_list_labels_file(self):
        import argparse, tempfile
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write("dina\nkarim\n")
        self.addCleanup(os.unlink, path)
        args = argparse.Namespace(labels=None, labels_file=path)
        self.assertEqual(atc._load_labels(args), ["dina", "karim"])

    def test_default_labels_file_is_repo_committed(self):
        # No flags: the checker must read the repo-committed labels file so the
        # workflow needs no token and no network (Khazen review on PR #47).
        import argparse
        args = argparse.Namespace(labels=None, labels_file=None)
        labels = atc._load_labels(args)
        self.assertIn("karim", labels)
        self.assertIn("dina", labels)


class SamirReviewTest(unittest.TestCase):
    """PR #47 review @407308ab: fenced exclusion, conflict rejection, CRLF."""

    def test_fenced_example_is_not_a_declaration(self):
        body = "Fixes thing\n\n```\nAgent: karim\n```\n"
        ok, detail = atc.check_pr_body(body, "karim")
        self.assertFalse(ok)
        self.assertIn("missing", detail)

    def test_tilde_fence_also_ignored(self):
        body = "notes\n\n~~~\nAgent: karim\n~~~\n"
        ok, _ = atc.check_pr_body(body, "karim")
        self.assertFalse(ok)

    def test_conflicting_agents_fail(self):
        body = "body\n\nAgent: dina\nAgent: karim\n"
        ok, detail = atc.check_pr_body(body, "karim")
        self.assertFalse(ok)
        self.assertIn("conflicting", detail.lower())

    def test_conflicting_agents_fail_even_if_branch_agent_present_first(self):
        body = "body\n\nAgent: karim\nAgent: dina\n"
        ok, _ = atc.check_pr_body(body, "karim")
        self.assertFalse(ok)

    def test_duplicate_identical_trailer_is_unambiguous(self):
        body = "body\n\nAgent: karim\nAgent: karim\n"
        ok, detail = atc.check_pr_body(body, "karim")
        self.assertTrue(ok, detail)

    def test_crlf_trailer_accepted(self):
        ok, detail = atc.check_pr_body("body\r\n\r\nAgent: karim\r\n", "karim")
        self.assertTrue(ok, detail)

    def test_crlf_conflict_still_detected(self):
        ok, _ = atc.check_pr_body("b\r\nAgent: karim\r\nAgent: dina\r\n", "karim")
        self.assertFalse(ok)

    def test_real_trailer_outside_fence_still_passes(self):
        body = "```\nAgent: dina\n```\nreal one:\n\nAgent: karim\n"
        ok, detail = atc.check_pr_body(body, "karim")
        self.assertTrue(ok, detail)


class PrFetchTest(unittest.TestCase):
    """Merger-lane mode: --pr owner/repo#num fetches body+branch with the
    token held in memory (rule 7), never in argv."""

    class FakeResp:
        def __init__(self, payload):
            import io, json
            self._io = io.BytesIO(json.dumps(payload).encode())
        def read(self):
            return self._io.read()

    def _opener(self, payload, captured):
        def open(req, timeout=30):
            captured.append(req)
            return self.FakeResp(payload)
        return open

    def test_parses_repo_and_number(self):
        self.assertEqual(atc.parse_pr_ref("ins-labs/ins-infra#658"), ("ins-labs/ins-infra", 658))

    def test_fetch_uses_header_not_argv_and_returns_body_branch(self):
        import tempfile
        fd, tpath = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write("dummy-token")
        self.addCleanup(os.unlink, tpath)
        captured = []
        payload = {"body": "x\n\nAgent: karim", "head": {"ref": "karim/work"}}
        result = atc.fetch_pr("ins-labs/ins-infra", 658, tpath,
                              opener=self._opener(payload, captured))
        self.assertEqual((result["body"], result["branch"]), ("x\n\nAgent: karim", "karim/work"))
        auth = captured[0].get_header("Authorization")
        self.assertEqual(auth, "Bearer dummy-token")
        self.assertIn("/repos/ins-labs/ins-infra/pulls/658", captured[0].full_url)

    def test_main_pr_mode_end_to_end(self):
        captured = []
        payload = {"body": "x\n\nAgent: karim", "head": {"ref": "karim/work"}}
        rc = atc.main(["--pr", "ins-labs/ins-infra#658", "--labels", "karim,dina",
                       "--token-file", "/nonexistent"],
                      opener=self._opener(payload, captured))
        self.assertEqual(rc, 0)


class AmenHoldTest(unittest.TestCase):
    """PR #48 holds @1109a444: redirect safety, SHA/state snapshot, 0600 enforcement."""

    def test_redirect_never_followed_and_auth_never_leaks(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        hits = []
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append((self.path, self.headers.get("Authorization")))
                if self.path.startswith("/repos/"):
                    self.send_response(302)
                    self.send_header("Location", "/evil")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")
            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        import tempfile
        fd, tpath = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write("secret-token")
        self.addCleanup(os.unlink, tpath)
        base = "http://127.0.0.1:%d" % srv.server_port
        with self.assertRaises(Exception):
            atc.fetch_pr("ins-labs/ins-infra", 658, tpath, api_base=base)
        evil = [h for h in hits if h[0] == "/evil"]
        self.assertEqual(evil, [], "redirect target was hit: %r" % evil)

    def test_fetch_returns_head_sha_and_state(self):
        captured = []
        payload = {"body": "b", "head": {"ref": "karim/w", "sha": "abc123"}, "state": "open"}
        class FakeResp:
            def __init__(self, p):
                import io, json
                self._io = io.BytesIO(json.dumps(p).encode())
            def read(self):
                return self._io.read()
        result = atc.fetch_pr("ins-labs/ins-infra", 1, None,
                              opener=lambda req, timeout=30: FakeResp(payload))
        self.assertEqual(result["head_sha"], "abc123")
        self.assertEqual(result["state"], "open")

    def test_world_readable_token_file_refused(self):
        import tempfile
        fd, tpath = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write("tok")
        os.chmod(tpath, 0o644)
        self.addCleanup(os.unlink, tpath)
        with self.assertRaises(PermissionError):
            atc.fetch_pr("ins-labs/ins-infra", 1, tpath,
                         opener=lambda req, timeout=30: None)

    def test_0600_token_file_accepted(self):
        import tempfile
        fd, tpath = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write("tok")
        os.chmod(tpath, 0o600)
        self.addCleanup(os.unlink, tpath)
        class FakeResp:
            def read(self):
                return b'{"body":"b","head":{"ref":"karim/w","sha":"s"},"state":"open"}'
        result = atc.fetch_pr("ins-labs/ins-infra", 1, tpath,
                              opener=lambda req, timeout=30: FakeResp())
        self.assertEqual(result["branch"], "karim/w")


class EnvEndpointSeamRegressionTest(unittest.TestCase):
    """Samir's #50 PoC (prove50): the checker must NOT honour any env endpoint
    override - a forged base URL must receive nothing, token or otherwise."""

    def test_env_override_receives_no_request(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        seen = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"body":"Agent: karim","head":{"ref":"karim/x","sha":"fake"},"state":"open"}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        import tempfile
        fd, tpath = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write("FAKE-ONLY-TEST-TOKEN")
        os.chmod(tpath, 0o600)
        self.addCleanup(os.unlink, tpath)
        for var in ("AGENT_TRAILER_API_BASE", "AGENT_TRAILER_API_URL", "GITHUB_API_URL"):
            os.environ[var] = "http://127.0.0.1:%d" % srv.server_port
            self.addCleanup(os.environ.pop, var, None)
        try:
            atc.main(["--pr", "owner/repo#1", "--token-file", tpath])
        except Exception:
            pass  # real api.github.com rejects the fake token; either way...
        self.assertEqual(seen, [], "env override endpoint was contacted: %r" % seen)
