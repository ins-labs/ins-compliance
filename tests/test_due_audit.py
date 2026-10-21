"""Tests for due_backfill --audit mode (issue #24)."""
import importlib.util, os, unittest

spec = importlib.util.spec_from_file_location(
    "due_backfill", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "due_backfill.py"))
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


def it(number, body, created="2026-10-21T10:00:00Z", labels=(), pr=False):
    d = {"number": number, "body": body, "created_at": created,
         "labels": [{"name": x} for x in labels]}
    if pr:
        d["pull_request"] = {"url": "..."}
    return d


class AuditRowsTest(unittest.TestCase):
    def test_missing_due_reported(self):
        rows = db.audit_rows("ins-infra", [it(1, "no due here")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["repo"], "ins-infra")
        self.assertEqual(rows[0]["number"], 1)
        self.assertEqual(rows[0]["created"], "2026-10-21")

    def test_first_line_due_clean(self):
        self.assertEqual(db.audit_rows("r", [it(1, "Due: 2026-10-28\nrest")]), [])

    def test_due_not_first_line_still_missing(self):
        rows = db.audit_rows("r", [it(1, "Owner: x\nDue: 2026-10-28")])
        self.assertEqual(len(rows), 1)

    def test_prs_excluded(self):
        self.assertEqual(db.audit_rows("r", [it(1, "x", pr=True)]), [])

    def test_owner_label_extracted(self):
        rows = db.audit_rows("r", [it(1, "x", labels=["agent:malek", "other"])])
        self.assertEqual(rows[0]["owner"], "malek")

    def test_no_owner_label(self):
        self.assertIsNone(db.audit_rows("r", [it(1, "x")])[0]["owner"])


if __name__ == "__main__":
    unittest.main()
