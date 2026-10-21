"""Tests for tools/due_backfill.py (issue #22)."""
import importlib.util, os, sys, unittest

spec = importlib.util.spec_from_file_location(
    "due_backfill", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "due_backfill.py"))
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


class InsertDueTest(unittest.TestCase):
    def test_empty_body(self):
        self.assertEqual(db.insert_due("", "2026-10-28"), "Due: 2026-10-28\n")

    def test_body_preserved_byte_for_byte(self):
        body = "Owner: agent:karim\n\nsome text\nwith trailing spaces   \n"
        out = db.insert_due(body, "2026-10-28")
        self.assertEqual(out, "Due: 2026-10-28\n" + body)

    def test_existing_first_line_due_skipped(self):
        body = "Due: 2026-10-01\nrest\n"
        self.assertIsNone(db.insert_due(body, "2026-10-28"))

    def test_due_not_on_first_line_is_not_a_skip(self):
        body = "Owner: x\nDue: 2026-10-01\n"
        out = db.insert_due(body, "2026-10-28")
        self.assertTrue(out.startswith("Due: 2026-10-28\nOwner: x\n"))

    def test_bad_date_rejected(self):
        with self.assertRaises(ValueError):
            db.insert_due("", "28-10-2026")
        with self.assertRaises(ValueError):
            db.insert_due("", "2026-13-01")


if __name__ == "__main__":
    unittest.main()
