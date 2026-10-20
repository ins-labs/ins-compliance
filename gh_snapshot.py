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

Feed additions (issue #5): last_editor per issue (actor of the newest issue
event or comment, falling back to the issue author), project fields
Status/Due/Blocked-by from the org's "ins-work" project when a read:project
token exists (GH_PROJECT_TOKEN_FILE), and native blocked-by links (REST issue
dependencies) as the fallback when no project token is set. V5 silence windows:
In progress = R17 threshold minutes, anything else (incl. Todo) = 24 h.

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


def read_token(env, var="GH_TOKEN_FILE"):
    path = env.get(var)
    if not path:
        raise SystemExit("%s is required (path to a root-only 0600 read-only PAT file)" % var)
    try:
        st = os.stat(path)
        mode = stat.S_IMODE(st.st_mode)
    except OSError:
        raise SystemExit(f"{var} {path} is not a regular file")
    if st.st_uid not in (0, os.geteuid()):
        raise SystemExit(f"{var} must be owned by root or the current user")
    if mode not in ALLOWED_MODES:
        raise SystemExit(f"{var} has insecure permissions {mode:o} (allowed: 400/440/600/640)")
    token = open(path).read().strip()
    if not token:
        raise SystemExit(f"{var} is empty")
    return token


class AuthSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib replays request headers across redirects; drop Authorization
    when the redirect leaves the API host so the token cannot leak."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        from urllib.parse import urlparse
        if urlparse(new.full_url).hostname != urlparse(req.full_url).hostname:
            new.headers.pop("Authorization", None)
        return new


class Gh:
    def __init__(self, token, api=API, opener=None):
        self.api = api
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        if opener is None:
            opener = urllib.request.build_opener(AuthSafeRedirectHandler()).open
        self._open = opener

    def graphql(self, query, variables=None):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        headers = dict(self._headers, **{"Content-Type": "application/json"})
        req = urllib.request.Request(self.api + "/graphql", data=body, headers=headers)
        try:
            with self._open(req, timeout=60) as r:
                payload = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                ep = json.loads(e.read() or b"{}")
            except Exception:
                ep = {}
            raise GhError(e.code, ep) from None
        if payload.get("errors"):
            raise GhError(200, payload["errors"])
        return payload

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
    # wrong under DST in the host's local zone (Amen PR #3).
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
    due = parse_due(it.get("body") or "")
    return {"repo": repo, "number": it["number"], "state": it["state"],
            "agent": agent_of(labels), "labels": labels,
            "assignees": [a["login"] for a in it.get("assignees", [])],
            "due": due, "due_source": "body" if due else None,
            "owner_hint": parse_owner(it.get("body") or ""),
            "blocked_by": blocked, "blocked_line_present": blocked_line,
            "blocked_by_closed": [],
            "status": None, "last_editor": None,
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


def editor_of(gh, repo, number, issue, org=ORG):
    """Actor of the newest issue event or comment; falls back to the author."""
    best_ts, best = -1, None
    events = list_paginated(gh, f"/repos/{org}/{repo}/issues/{number}/events")
    comments = list_paginated(gh, f"/repos/{org}/{repo}/issues/{number}/comments")
    for ev in events:
        ts = to_epoch(ev["created_at"]) if ev.get("created_at") else 0
        actor = (ev.get("actor") or {}).get("login")
        if actor and ts >= best_ts:
            best_ts, best = ts, actor
    for c in comments:
        ts = to_epoch(c["created_at"]) if c.get("created_at") else 0
        actor = (c.get("user") or {}).get("login")
        if actor and ts >= best_ts:
            best_ts, best = ts, actor
    if best is None:
        best = (issue.get("user") or {}).get("login")
    return best


PROJECT_FIND_Q = """query($org:String!){organization(login:$org){projectsV2(first:50){nodes{id title}}}}"""
PROJECT_ITEMS_Q = """query($id:ID!,$after:String){node(id:$id){... on ProjectV2{items(first:100,after:$after){
nodes{content{... on Issue{number repository{name}}} fieldValues(first:20){nodes{
... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}
... on ProjectV2ItemFieldDateValue{date field{... on ProjectV2FieldCommon{name}}}
... on ProjectV2ItemFieldIssueValue{issues(first:10){nodes{number state}} field{... on ProjectV2FieldCommon{name}}}
}}} pageInfo{hasNextPage endCursor}}}}}"""
PROJECT_TITLE = "ins-work"


def project_fields(pg, org=ORG):
    """ins-work project -> {(repo, number): {status, due, blocked: [(num, state)]}}.
    Returns None when the project is not found."""
    data = pg.graphql(PROJECT_FIND_Q, {"org": org})
    nodes = (((data.get("data") or {}).get("organization") or {}).get("projectsV2") or {}).get("nodes") or []
    pid = None
    for n in nodes:
        if (n.get("title") or "").strip().lower() == PROJECT_TITLE:
            pid = n.get("id")
            break
    if not pid:
        return None
    out, after = {}, None
    while True:
        data = pg.graphql(PROJECT_ITEMS_Q, {"id": pid, "after": after})
        items = ((data.get("data") or {}).get("node") or {}).get("items") or {}
        for node in items.get("nodes") or []:
            content = node.get("content") or {}
            repo = (content.get("repository") or {}).get("name")
            number = content.get("number")
            if not repo or number is None:
                continue  # draft items and non-issue cards carry no hygiene data
            status, due, blocked = None, None, []
            for fv in (node.get("fieldValues") or {}).get("nodes") or []:
                fname = ((fv.get("field") or {}).get("name") or "").strip().lower()
                if fname == "status" and fv.get("name"):
                    status = fv["name"]
                elif fname == "due" and fv.get("date"):
                    due = fv["date"]
                elif fname == "blocked by":
                    if fv.get("issues"):
                        blocked += [(i["number"], (i.get("state") or "").lower())
                                    for i in (fv["issues"].get("nodes") or []) if i.get("number")]
                    elif fv.get("issue"):
                        i = fv["issue"]
                        blocked.append((i["number"], (i.get("state") or "").lower()))
            out[(repo, number)] = {"status": status, "due": due, "blocked": blocked}
        page = items.get("pageInfo") or {}
        if not page.get("hasNextPage") or not page.get("endCursor"):
            return out
        after = page.get("endCursor")


def apply_blocked(row, native):
    """Merge native blocked-by links [(num, state)] into a snapshot row."""
    if not native:
        return
    have = set(row["blocked_by"])
    for num, state in native:
        if num not in have:
            row["blocked_by"].append(num)
            have.add(num)
        if state == "closed" and num not in row["blocked_by_closed"]:
            row["blocked_by_closed"].append(num)
    row["blocked_by"].sort()
    row["blocked_line_present"] = True
    row.setdefault("_native", set()).update(n for n, _ in native)


def snapshot(env=None, repos=None, org=ORG, opener=None, gh=None):
    env = os.environ if env is None else env
    gh = gh or Gh(read_token(env), opener=opener)
    if repos is None:
        repos = [r["name"] for r in list_paginated(gh, f"/orgs/{org}/repos")]
    # Project fields (Status/Due/Blocked-by) only when the read:project token
    # exists; the snapshot stays honest without it (status None, meta says why).
    pfields, project_state = None, "no_token"
    if env.get("GH_PROJECT_TOKEN_FILE"):
        try:
            pg = Gh(read_token(env, "GH_PROJECT_TOKEN_FILE"), opener=opener)
            pfields = project_fields(pg, org)
            project_state = "ok" if pfields is not None else "not_found"
        except GhError:
            pfields, project_state = None, "error"
    rows, prs, failed = [], [], []
    editor_failed = 0
    blocked_native = "project" if pfields is not None else "rest"
    for repo in repos:
        try:
            items = list_paginated(gh, f"/repos/{org}/{repo}/issues?state=open")
        except GhError:
            failed.append(repo)
            continue
        open_numbers = set()
        by_number = {}
        for it in items:
            if "pull_request" in it:
                body = it.get("body") or ""
                prs.append({"repo": repo, "number": it["number"],
                            # strict on purpose: a bare "#120" without a
                            # close/fix/ref word does not count as linked
                            "has_issue_ref": bool(LINKED_ISSUE_RE.search(body)),
                            "updated_ts": to_epoch(it["updated_at"])})
                continue
            row = row_of(it, repo)
            rows.append(row)
            by_number[it["number"]] = (row, it)
            open_numbers.add(it["number"])
        # V10 feed: editor of last update (newest event or comment wins)
        for number, (row, it) in by_number.items():
            try:
                row["last_editor"] = editor_of(gh, repo, number, it, org)
            except GhError:
                editor_failed += 1
        # Project fields + blocked-by links
        for number, (row, it) in by_number.items():
            if pfields is not None:
                pf = pfields.get((repo, number))
                if pf:
                    row["status"] = pf["status"]
                    if not row["due"] and pf["due"]:
                        row["due"], row["due_source"] = pf["due"], "project"
                    apply_blocked(row, pf["blocked"])
            elif blocked_native == "rest":
                try:
                    deps = list_paginated(gh, f"/repos/{org}/{repo}/issues/{number}/dependencies/blocked_by")
                    apply_blocked(row, [(d["number"], (d.get("state") or "").lower())
                                        for d in deps if d.get("number")])
                except GhError:
                    blocked_native = "unsupported"  # circuit break: one probe, then stop
        # V6 support: resolve the state of referenced blockers not in the open
        # set and not already classified by native/project links
        for row in rows:
            if row["repo"] != repo:
                continue
            closed = list(row["blocked_by_closed"])
            known = set(closed) | row.get("_native", set())
            for n in row["blocked_by"]:
                if n in open_numbers or n in known:
                    continue
                try:
                    one = gh.get(f"/repos/{org}/{repo}/issues/{n}")
                    st = one.get("state") if isinstance(one, dict) else "unknown"
                except GhError:
                    st = "unknown"
                if st == "closed":
                    closed.append(n)
            row["blocked_by_closed"] = closed
            row.pop("_native", None)
    rows.sort(key=lambda r: (r["repo"], r["number"]))
    meta = {"repos_failed": failed, "repos_ok": [r for r in repos if r not in failed],
            "issues_seen": len(rows), "prs_seen": len(prs),
            "project": project_state, "blocked_native": blocked_native,
            "editor_failed": editor_failed}
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
