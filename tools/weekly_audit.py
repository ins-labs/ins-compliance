#!/usr/bin/env python3
"""Weekly one-command compliance report (issue #34).

Runs the two read-only audits already in tools/due_backfill.py -
missing first-line Due (--audit, issue #24) and Agent-trailer adoption
(--audit-trailers, issue #32) - and folds them into a single JSON report
on stdout so the weekly compliance cron tracks both numbers with no
hand work. LLM-free, stdlib-only, zero writes.

Usage: GH_TOKEN_FILE=<file> python3 tools/weekly_audit.py [--since YYYY-MM-DD]
Output: {"date", "since", "missing_due", "missing_due_rows",
         "trailers": {"total", "with", "pct"}, "trailer_rows"}
Per-row details (the JSONL lines from both audits) go to stderr so the
stdout report stays machine-clean.
"""
import datetime
import json
import os
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
BACKFILL = os.path.join(TOOLS, "due_backfill.py")


def aggregate(missing_rows, trailer_rows, since):
    """Pure: audit rows + trailer/misattribution rows -> weekly report.
    Misattribution rows (issue #36) carry a "trailer" key, adoption rows a
    "total" key; they are split here, not summed together."""
    adoption = [r for r in trailer_rows if "total" in r]
    misattr = [r for r in trailer_rows if "trailer" in r]
    total = sum(r.get("total", 0) for r in adoption)
    with_t = sum(r.get("with", 0) for r in adoption)
    return {
        "date": datetime.date.today().isoformat(),
        "since": since,
        "missing_due": len(missing_rows),
        "missing_due_rows": list(missing_rows),
        "trailers": {"total": total, "with": with_t,
                     "pct": (100 * with_t // total) if total else 0},
        "trailer_rows": adoption,
        "misattributed": len(misattr),
        "misattributed_rows": misattr,
    }


def run_audit(args):
    """Run due_backfill.py with args; return parsed stdout JSONL rows."""
    env = dict(os.environ)
    out = subprocess.run([sys.executable, BACKFILL] + args,
                         capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise SystemExit("due_backfill %s failed: %s" % (args, out.stderr.strip()))
    if out.stderr:
        sys.stderr.write(out.stderr)
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def main(argv):
    since = next((argv[i + 1] for i, a in enumerate(argv)
                  if a == "--since" and i + 1 < len(argv)),
                 (datetime.date.today() - datetime.timedelta(days=7)).isoformat())
    missing = run_audit(["--audit"])
    trailers = run_audit(["--audit-trailers", "--since", since])
    print(json.dumps(aggregate(missing, trailers, since), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
