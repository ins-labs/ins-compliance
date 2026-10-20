#!/usr/bin/env python3
"""Read-only GitHub issues snapshot for the R17 check (github_issue_hygiene).

Fetches open issues (never PRs) for the given ins-labs repos and prints one
JSON row per issue: repo, number, state, agent (from the agent:<name> label),
labels, assignees, due (first body line "Due: YYYY-MM-DD"), blocked_by
(#n parsed from a "Blocked by" body line), blocked_line_present,
blocked_by_closed (referenced blockers whose state is closed), created_ts,
updated_ts. Open PRs are collected too (number + has_issue_ref) for V9.

Token: GH_TOKEN_FILE (root-only, 400/440/600/640) holding a read-only PAT -
never an exported env var (same rule as ins-infra #162). No text (title/body)
is emitted, only ids and metadata. Any repo failure is collected and makes
the exit status 1: a partial snapshot must never read as clean.

Usage: gh_snapshot.py META_JSON [REPO ...] > issues.jsonl
"""
import base64, calendar, json, os, re, stat, sys, time, urllib.error, urllib.request

API = "https://api.github.com"
ORG = "ins-labs"
ALLOWED_MODES = (0o400, 0o440, 0o600, 0o640)
DUE_RE = re.compile(r"^Due: (\d{4}-\d{2}-\d{2})\s*$")
BLOCKED_LINE_RE = re.compile(r"^Blocked by:? (.+)$", re.I | re.M)
ISSUE_REF_RE = re.compile(r"(?<![\w/])#(\d+)")
LINKED_ISSUE_RE = re.compile(r"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?|refs?)\b[^.\n]{0,40}#\d+")
OWNER_RE = re.compile(r"^Owner: ([^.\n]+)", re.I | re.M)


class GhError(RuntimeError):
    def __init__(self, status, payload):
        super().__init__(f"github {status}: {str(payload)[:200]}")
        self.status = status
        self.payload = payload


def read_token(env):
    path = env.get("GH_TOKEN_FILE")
    if not path:
        raise SystemExit("GH_TOKEN_FILE is required (path to a root-only 0600 read-only PAT file)")
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        raise SystemExit(f"GH_TOKEN_FILE {path} is not a regular file")
    if mode not in ALLOWED_MODES:
        raise SystemExit(f"GH_TOKEN_FILE has insecure permissions {mode:o} (allowed: 400/440/600/640)")
    token = open(path).read().strip()
    if not token:
        raise SystemExit("GH_TOKEN_FILE is empty")
    return token


class Gh:
    def __init__(self, token, api=API, opener=None):
        self.api = api
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        self._open = opener or urllib.request.urlopen

    def get(self, path):
        req = urllib.request.Request(self.api + path, headers=self._headers)
        try:
            with self._open(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"{}")
            except Exception:
                payload = {}
            raise GhError(e.code, payload) from None


def to_epoch(s):
    # GitHub timestamps are UTC ("Z"): parse with timegm. mktime-timezone is
    # wrong under DST in the host's local zone (Ayman PR #3).
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))


def parse_due(body):
    if not body:
        return None
    m = DUE_RE.match(body.split("\n", 1)[0].strip())
    return m.group(1) if m else None


def parse_blocked(body):
    """Returns (numbers, line_present). #n refs only; owner/repo#n is ignored."""
    if not body:
        return [], False
    m = BLOCKED_LINE_RE.search(body)
    if not m:
        return [], False
    nums = [int(x) for x in ISSUE_REF_RE.findall(m.group(1))]
    return nums, True


def parse_owner(body):
    """Owner hint from a 'Owner: <name or id>' body line (Dina FINAL 09:22).
    The hint stops at the first '.' or newline; resolution against the
    registry happens in the checker, which owns the registry data."""
    if not body:
        return None
    m = OWNER_RE.search(body)
    return m.group(1).strip() if m else None


def agent_of(labels):
    for name in labels:
        if name.startswith("agent:"):
            return name.split(":", 1)[1]
    return None


def row_of(it, repo):
    labels = [l["name"] for l in it.get("labels", [])]
    blocked, blocked_line = parse_blocked(it.get("body") or "")
    return {"repo": repo, "number": it["number"], "state": it["state"],
            "agent": agent_of(labels), "labels": labels,
            "assignees": [a["login"] for a in it.get("assignees", [])],
            "due": parse_due(it.get("body") or ""),
            "owner_hint": parse_owner(it.get("body") or ""),
            "blocked_by": blocked, "blocked_line_present": blocked_line,
            "created_ts": to_epoch(it["created_at"]), "updated_ts": to_epoch(it["updated_at"])}


def list_paginated(gh, path):
    out, page = [], 1
    while True:
        batch = gh.get(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
        if not batch:
            return out
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1
        if page > 20:
            raise GhError(0, "pagination cap")


def snapshot(env=None, repos=None, org=ORG, opener=None, gh=None):
    env = os.environ if env is None else env
    gh = gh or Gh(read_token(env), opener=opener)
    if repos is None:
        repos = [r["name"] for r in list_paginated(gh, f"/orgs/{org}/repos")]
    rows, prs, failed = [], [], []
    for repo in repos:
        try:
            items = list_paginated(gh, f"/repos/{org}/{repo}/issues?state=open")
        except GhError:
            failed.append(repo)
            continue
        open_numbers = set()
        for it in items:
            if "pull_request" in it:
                body = it.get("body") or ""
                prs.append({"repo": repo, "number": it["number"],
                            # strict on purpose: a bare "#120" without a
                            # close/fix/ref word does not count as linked
                            "has_issue_ref": bool(LINKED_ISSUE_RE.search(body)),
                            "updated_ts": to_epoch(it["updated_at"])})
                continue
            rows.append(row_of(it, repo))
            open_numbers.add(it["number"])
        # V6 support: resolve the state of referenced blockers not in the open set
        for row in rows:
            if row["repo"] != repo:
                continue
            closed = []
            for n in row["blocked_by"]:
                if n in open_numbers:
                    continue
                try:
                    one = gh.get(f"/repos/{org}/{repo}/issues/{n}")
                    st = one.get("state") if isinstance(one, dict) else "unknown"
                except GhError:
                    st = "unknown"
                if st == "closed":
                    closed.append(n)
            row["blocked_by_closed"] = closed
    rows.sort(key=lambda r: (r["repo"], r["number"]))
    meta = {"repos_failed": failed, "repos_ok": [r for r in repos if r not in failed],
            "issues_seen": len(rows), "prs_seen": len(prs)}
    return (rows, prs), meta


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    meta_path = argv[1]
    repos = argv[2:] or None
    (rows, prs), meta = snapshot(repos=repos)
    with open(meta_path, "w") as fh:
        json.dump(meta, fh, indent=1)
    os.chmod(meta_path, 0o600)
    for row in rows:
        row["kind"] = "issue"
        print(json.dumps(row, ensure_ascii=False))
    for pr in prs:
        pr["kind"] = "pr"
        print(json.dumps(pr, ensure_ascii=False))
    return 1 if meta["repos_failed"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
