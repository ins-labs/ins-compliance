#!/usr/bin/env python3
"""Phase-1 first-line Due backfill (issue #22). DRY-RUN by default.

Dina's finding: 72 open issues across 13 org repos lack a first-line
"Due: YYYY-MM-DD". Plan amendments (Dina): owner-supplied dates only; the
Due line is inserted as the new first body line with the rest of the body
byte-for-byte unchanged; per-repo dry-run diff shown to the owner first;
small batches; no @mentions added.

Input mapping JSON: {"repo": {"<issue_number>": "YYYY-MM-DD", ...}, ...}

Safety:
- --apply is required for any write; without it only diffs are printed.
- --allow REPO (repeatable) limits work to repos whose owners agreed.
- Issues already having a first-line Due are skipped.
- Optimistic concurrency: updated_at is re-read right before each PATCH;
  a changed issue is skipped and reported.
- Token via GH_TOKEN_FILE (mode 400/440/600/640), never printed.
- 1 s pause between writes (small batches).
"""
import datetime, difflib, json, os, re, stat, sys, time, urllib.request

API = "https://api.github.com"
ORG = "ins-labs"
DUE_RE = re.compile(r"^Due: \d{4}-\d{2}-\d{2}\s*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def insert_due(body, date):
    """Return body with a first-line Due prepended, or None if one exists.
    The original body is preserved byte-for-byte after the new first line."""
    if not DATE_RE.match(date):
        raise ValueError("bad date: %r" % date)
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise ValueError("bad date: %r" % date)
    if DUE_RE.match(body.split("\n", 1)[0]):
        return None
    return "Due: %s\n%s" % (date, body)


def token():
    p = os.environ.get("GH_TOKEN_FILE")
    st = os.stat(p)
    if stat.S_IMODE(st.st_mode) not in (0o400, 0o440, 0o600, 0o640):
        raise SystemExit("GH_TOKEN_FILE mode must be 400/440/600/640")
    return open(p).read().strip()


def gh(method, path, tok, body=None):
    req = urllib.request.Request(API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"})
    return json.load(urllib.request.urlopen(req))


def main(argv):
    apply_mode = "--apply" in argv
    allow = {argv[i + 1] for i, a in enumerate(argv) if a == "--allow" and i + 1 < len(argv)}
    mapping_path = next(a for a in argv[1:] if not a.startswith("--") and a not in allow)
    mapping = json.load(open(mapping_path))
    tok = token()
    planned = done = skipped = failed = 0
    for repo, issues in mapping.items():
        if allow and repo not in allow:
            continue
        for num_s, date in issues.items():
            num = int(num_s)
            try:
                it = gh("GET", "/repos/%s/%s/issues/%d" % (ORG, repo, num), tok)
                body = it.get("body") or ""
                new_body = insert_due(body, date)
            except ValueError as e:
                print("BAD DATE %s#%d: %s" % (repo, num, e)); failed += 1; continue
            if new_body is None:
                skipped += 1; continue
            planned += 1
            diff = "".join(difflib.unified_diff(body.splitlines(True), new_body.splitlines(True),
                "%s#%d body" % (repo, num), "%s#%d body+" % (repo, num), n=1))
            if not apply_mode:
                sys.stdout.write(diff)
                continue
            fresh = gh("GET", "/repos/%s/%s/issues/%d" % (ORG, repo, num), tok)
            if fresh.get("updated_at") != it.get("updated_at"):
                print("STALE %s#%d, skipped" % (repo, num)); failed += 1; continue
            gh("PATCH", "/repos/%s/%s/issues/%d" % (ORG, repo, num), tok, {"body": new_body})
            done += 1; time.sleep(1)
    print("planned=%d applied=%d skipped_has_due=%d failed=%d mode=%s" %
          (planned, done, skipped, failed, "APPLY" if apply_mode else "DRY-RUN"), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
