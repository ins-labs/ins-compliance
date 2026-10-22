"""Registry-aware misattribution (issue #38): labels + aliases come from
ins-infra registry/agents.yaml; non-agent prefixes/labels are skipped."""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import due_backfill as db

REGISTRY = """version: 1
label_prefix: "agent:"
agents:
  - {label: nader, agent_id: agent-1, status: active}
  - {label: kaleem, agent_id: agent-2, status: active, aliases: [salma]}
  - {label: amen, agent_id: agent-3, status: active, aliases: [ayman]}
  - {label: karim, agent_id: agent-4, status: active}
github_logins:
  - {login: ins-sherbeeny, kind: shared-agent-login}
"""


class ParseRegistryTest(unittest.TestCase):
    def test_labels_and_aliases(self):
        agents = db.parse_agents_yaml(REGISTRY)
        self.assertEqual(agents, {"nader", "kaleem", "salma", "amen", "ayman", "karim"})

    def test_github_logins_section_not_agents(self):
        agents = db.parse_agents_yaml(REGISTRY)
        self.assertNotIn("ins-sherbeeny", agents)

    def test_empty(self):
        self.assertEqual(db.parse_agents_yaml(""), set())
        self.assertEqual(db.parse_agents_yaml("agents:\n"), set())


class RegistryAwareTest(unittest.TestCase):
    AGENTS = {"karim", "nader", "amen", "kaleem", "salma", "gawaher"}

    def test_branch_prefix_not_agent_skipped(self):
        self.assertIsNone(db.branch_agent("feat/health-endpoint", self.AGENTS))
        self.assertIsNone(db.branch_agent("fix/typo", self.AGENTS))

    def test_branch_prefix_agent_kept(self):
        self.assertEqual(db.branch_agent("karim/weekly-audit-34", self.AGENTS), "karim")

    def test_alias_branch_counts(self):
        self.assertEqual(db.branch_agent("salma/ring-fix", self.AGENTS), "salma")

    def test_no_registry_legacy_behavior(self):
        self.assertEqual(db.branch_agent("feat/x"), "feat")

    def test_misattr_skips_non_agent_trailer(self):
        row = db.misattr_row("ins-bridge", "0a4de268" + "0" * 32,
                             "thing (#235)\n\nAgent: ins-sherbeeny\n",
                             "fix/bridge-retry", self.AGENTS)
        self.assertIsNone(row)

    def test_misattr_still_flags_known_agents(self):
        row = db.misattr_row("ins-compliance", "51e0939d" + "0" * 32,
                             "audit (#33)\n\nAgent: agent:gawaher\n",
                             "karim/audit-trailers", self.AGENTS)
        self.assertIsNotNone(row)
        self.assertEqual(row["branch_agent"], "karim")


if __name__ == "__main__":
    unittest.main()

class EmptyRegistryWarnTest(unittest.TestCase):
    def test_fetch_ok_empty_parse_warns(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            agents, fetched = db.load_registry(lambda: "agents:\n")
        assert fetched is True
        assert agents == set()
        assert "WARNING" in err.getvalue()

    def test_fetch_failure_no_warn_and_fallback(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            agents, fetched = db.load_registry(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert fetched is False
        assert agents is None
        assert "WARNING" not in err.getvalue()
