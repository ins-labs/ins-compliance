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
TRAILER_RE = re.compile(r"^Agent:\s*\S+\s*$", re.M)
SUFFIX_RE = re.compile(r"\(agent:[a-z0-9-]+\)")



PRNUM_RE = re.compile(r"\(#(\d+)\)\s*$")


def _norm_agent(label):
    """Normalize an agent label: strip one leading 'agent:' prefix."""
    return label.split(":", 1)[1] if label.startswith("agent:") else label


def pr_number(subject):
    """Pure: squash-merge subject -> PR number, or None (issue #36)."""
    m = PRNUM_RE.search(subject)
    return int(m.group(1)) if m else None


def trailer_label(msg):
    """Pure: commit message -> normalized Agent trailer label, or None."""
    m = re.search(r"^Agent:\s*(\S+)\s*$", msg, re.M)
    return _norm_agent(m.group(1)) if m else None


def branch_agent(branch):
    """Pure: PR head branch -> owner prefix, or None for unowned names."""
    parts = (branch or "").split("/", 1)
    return parts[0] if len(parts) == 2 and parts[0] else None


def misattr_row(repo, sha, msg, branch):
    """Pure: squash-merge commit + its PR head branch -> mismatch row or
    None (issue #36). Only flags when all three facts exist and differ:
    a PR-numbered subject, an Agent trailer, an owned branch prefix."""
    subject = msg.split("\n", 1)[0]
    pr = pr_number(subject)
    label = trailer_label(msg)
    agent = branch_agent(branch)
    if pr is None or label is None or agent is None or label == agent:
        return None
    return {"repo": repo, "sha": sha[:8], "pr": pr,
            "trailer": label, "branch_agent": agent}

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


def audit_rows(repo, issues):
    """Pure: open issues missing a first-line Due -> report rows (issue #24).
    PRs are excluded; the owner is the agent:<name> label when present."""
    rows = []
    for it in issues:
        if it.get("pull_request"):
            continue
        body = it.get("body") or ""
        if DUE_RE.match(body.split("\n", 1)[0]):
            continue
        owner = None
        for lab in it.get("labels") or []:
            name = lab.get("name") or ""
            if name.startswith("agent:"):
                owner = name.split(":", 1)[1]
                break
        rows.append({"repo": repo, "number": it["number"], "owner": owner,
                     "created": (it.get("created_at") or "")[:10]})
    return rows


def trailer_rows(repo, commits):
    """Pure: default-branch commits -> Agent-trailer adoption row (issue #32).
    Counts the ADR-0003 `Agent: <label>` trailer and the transition form
    `(agent:<name>)` in the subject line."""
    with_t = 0
    for c in commits:
        msg = ((c.get("commit") or {}).get("message")) or ""
        if TRAILER_RE.search(msg) or SUFFIX_RE.search(msg.split("\n", 1)[0]):
            with_t += 1
    total = len(commits)
    return {"repo": repo, "total": total, "with": with_t,
            "without": total - with_t,
            "pct": (100 * with_t // total) if total else 0}


def list_paginated(tok, path):
    out, url = [], path
    sep = "&" if "?" in path else "?"
    page = 1
    while True:
        batch = gh("GET", "%s%sper_page=100&page=%d" % (path, sep, page), tok)
        if not batch:
            return out
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1


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


def run_audit(tok, allow):
    repos = sorted(allow) if allow else [
        r["name"] for r in list_paginated(tok, "/orgs/%s/repos?type=all" % ORG)]
    total = 0
    for repo in repos:
        rows = audit_rows(repo, list_paginated(
            tok, "/repos/%s/%s/issues?state=open" % (ORG, repo)))
        for row in rows:
            print(json.dumps(row))
        total += len(rows)
    print("audit: repos=%d missing_due=%d (read-only, zero writes)" % (len(repos), total),
          file=sys.stderr)


def run_audit_trailers(tok, allow, since):
    repos = sorted(allow) if allow else [
        r["name"] for r in list_paginated(tok, "/orgs/%s/repos?type=all" % ORG)]
    t = w = m = 0
    for repo in repos:
        commits = list_paginated(
            tok, "/repos/%s/%s/commits?since=%sT00:00:00Z" % (ORG, repo, since))
        row = trailer_rows(repo, commits)
        print(json.dumps(row))
        t += row["total"]
        w += row["with"]
        for c in commits:
            msg = ((c.get("commit") or {}).get("message")) or ""
            pr = pr_number(msg.split("\n", 1)[0])
            if pr is None or trailer_label(msg) is None:
                continue
            try:
                head = (gh("GET", "/repos/%s/%s/pulls/%d" % (ORG, repo, pr), tok)
                        .get("head") or {}).get("ref") or ""
            except Exception:
                continue
            bad = misattr_row(repo, c.get("sha") or "", msg, head)
            if bad:
                print(json.dumps(bad))
                m += 1
    print("audit-trailers: repos=%d commits=%d with_trailer=%d (%d%%) misattributed=%d since=%s (read-only, zero writes)"
          % (len(repos), t, w, (100 * w // t) if t else 0, m, since), file=sys.stderr)


def main(argv):
    if "--audit-trailers" in argv:
        allow = {argv[i + 1] for i, a in enumerate(argv) if a == "--allow" and i + 1 < len(argv)}
        since = next((argv[i + 1] for i, a in enumerate(argv) if a == "--since" and i + 1 < len(argv)),
                     (datetime.date.today() - datetime.timedelta(days=7)).isoformat())
        if not DATE_RE.match(since):
            raise SystemExit("bad --since date: %r" % since)
        run_audit_trailers(token(), allow, since)
        return
    if "--audit" in argv:
        allow = {argv[i + 1] for i, a in enumerate(argv) if a == "--allow" and i + 1 < len(argv)}
        run_audit(token(), allow)
        return
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
