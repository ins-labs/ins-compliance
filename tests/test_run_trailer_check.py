"""Subprocess tests for tools/run_trailer_check.sh (issue #49).

Builds a local stub 'ins-compliance' repo whose checker exits 0/1/7, then runs
the real script against it with INS_COMPLIANCE_PIN overridden, proving rc
propagation and zero worktree leaks. Failure branch: unknown pin -> non-zero.
"""
import os, stat, subprocess, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "tools", "run_trailer_check.sh")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class RunTrailerCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ["rm", "-rf", self.tmp])
        org = os.path.join(self.tmp, "org.git")
        seed = os.path.join(self.tmp, "seed")
        git("init", "-q", "--bare", org, cwd=self.tmp)
        git("clone", "-q", org, seed, cwd=self.tmp)
        git("config", "user.email", "t@t", cwd=seed)
        git("config", "user.name", "t", cwd=seed)
        git("checkout", "-qb", "main", cwd=seed)
        os.makedirs(os.path.join(seed, "tools"))
        os.makedirs(os.path.join(seed, "registry"))
        with open(os.path.join(seed, "tools", "agent_trailer_check.py"), "w") as fh:
            fh.write("import os, sys\nsys.exit(int(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'registry', 'rc.txt')).read().strip()))\n")
        self.shas = {}
        for rc in (0, 1, 7):
            with open(os.path.join(seed, "registry", "rc.txt"), "w") as fh:
                fh.write(str(rc))
            git("add", "-A", cwd=seed)
            git("commit", "-qm", "rc%d" % rc, cwd=seed)
            out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=seed,
                                 capture_output=True, text=True, check=True)
            self.shas[rc] = out.stdout.strip()
        git("push", "-q", "origin", "main", cwd=seed)
        git("clone", "-q", org, "ins-compliance", cwd=self.tmp)

    def _run(self, pin):
        env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN_FILE"}
        env["INS_COMPLIANCE_PIN"] = pin
        return subprocess.run(["bash", SCRIPT, "owner/repo#1", os.path.join(self.tmp, "ins-compliance")],
                              env=env, cwd=self.tmp,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode

    def _worktree_count(self):
        out = subprocess.run(["git", "-C", os.path.join(self.tmp, "ins-compliance"), "worktree", "list"],
                             capture_output=True, text=True, check=True)
        return len(out.stdout.strip().splitlines())

    def test_checker_exit_codes_propagate(self):
        for rc in (0, 1, 7):
            self.assertEqual(self._run(self.shas[rc]), rc, "checker exit %d" % rc)
        self.assertEqual(self._worktree_count(), 1, "worktree leak")

    def test_unknown_pin_is_nonzero(self):
        self.assertNotEqual(self._run("deadbeef" * 5), 0)
        self.assertEqual(self._worktree_count(), 1, "worktree leak")

    def test_script_is_bash_n_clean(self):
        rc = subprocess.run(["bash", "-n", SCRIPT]).returncode
        self.assertEqual(rc, 0)

    def test_script_is_executable(self):
        self.assertTrue(os.stat(SCRIPT).st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()


class RealCheckerNoTokenTest(unittest.TestCase):
    """Amen's #50 hold: run the script against a stub repo carrying the REAL
    checker + labels, with NO GH_TOKEN_FILE set and a mock API server, proving
    the no-token path reaches the API instead of dying on file perms."""

    def setUp(self):
        import json, threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        self.payload = {"body": "x\n\nAgent: karim",
                        "head": {"ref": "karim/w", "sha": "s"}, "state": "open"}
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(outer.payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.api_port = self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ["rm", "-rf", self.tmp])
        org = os.path.join(self.tmp, "org.git")
        seed = os.path.join(self.tmp, "seed")
        git("init", "-q", "--bare", org, cwd=self.tmp)
        git("clone", "-q", org, seed, cwd=self.tmp)
        git("config", "user.email", "t@t", cwd=seed)
        git("config", "user.name", "t", cwd=seed)
        git("checkout", "-qb", "main", cwd=seed)
        os.makedirs(os.path.join(seed, "tools"))
        os.makedirs(os.path.join(seed, "registry"))
        with open(os.path.join(REPO, "tools", "agent_trailer_check.py")) as fh:
            checker = fh.read()
        # Isolated test shim: only the STUB COPY is pointed at the mock API.
        # The production file keeps api.github.com hard-wired and gains no
        # env seam (Amen's security hold on #50).
        assert 'api_base="https://api.github.com"' in checker
        checker = checker.replace('api_base="https://api.github.com"',
                                  'api_base="http://127.0.0.1:%d"' % self.api_port)
        assert "AGENT_TRAILER_API_BASE" not in checker
        with open(os.path.join(seed, "tools", "agent_trailer_check.py"), "w") as fh:
            fh.write(checker)
        with open(os.path.join(seed, "registry", "agent-labels.txt"), "w") as fh:
            fh.write("dina\nkarim\n")
        git("add", "-A", cwd=seed)
        git("commit", "-qm", "real checker", cwd=seed)
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=seed,
                             capture_output=True, text=True, check=True)
        self.pin = out.stdout.strip()
        git("push", "-q", "origin", "main", cwd=seed)
        git("clone", "-q", org, "ins-compliance", cwd=self.tmp)

    def _run(self):
        env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN_FILE"}
        env["INS_COMPLIANCE_PIN"] = self.pin
        return subprocess.run(["bash", SCRIPT, "owner/repo#1", os.path.join(self.tmp, "ins-compliance")],
                              env=env, cwd=self.tmp,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode

    def test_no_token_pass_with_trailer(self):
        self.assertEqual(self._run(), 0)

    def test_no_token_fail_without_trailer(self):
        self.payload = {"body": "no trailer",
                        "head": {"ref": "karim/w", "sha": "s"}, "state": "open"}
        self.assertEqual(self._run(), 1)


class ProductionEndpointGuardTest(unittest.TestCase):
    def test_production_checker_has_no_env_api_seam(self):
        with open(os.path.join(REPO, "tools", "agent_trailer_check.py")) as fh:
            src = fh.read()
        self.assertNotIn("AGENT_TRAILER_API_BASE", src)
        self.assertIn('api_base="https://api.github.com"', src)
