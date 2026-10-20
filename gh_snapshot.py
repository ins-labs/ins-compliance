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
event or comment, falling back to the issue author; actor type and a
Co-authored-by/Owner trailer from a newest commit reference included for V10
attribution), project fields Status/Due from the org's "ins-work" project when
a read:project token exists (GH_PROJECT_TOKEN_FILE), and native blocked-by
links from the REST issue-dependencies endpoint (the circuit breaker trips on
404/422 only, never on transient 5xx). Editor and dependency calls are cached
by issue updated_ts when GH_SNAPSHOT_STATE points at a writable state file, so
only issues changed since the last snapshot cost API calls. V5 silence
windows: In progress = R17 threshold minutes, anything else (incl. Todo) = 24 h.

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
            "status": None, "last_editor": None, "last_editor_type": None, "last_editor_trailer": None,
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


TRAILER_RE = re.compile(r"(?:^|\n)\s*(?:Co-authored-by:\s*([^<\n]+?)\s*<[^>]*>|Owner:\s*([^<\n.]+))", re.I)


def editor_of(gh, repo, number, issue, org=ORG):
    """Newest touch: (login, actor_type, trailer). Actor of the newest issue
    event or comment; falls back to the issue author. When the newest event is
    a commit reference (referenced/closed/cross-referenced with a commit_id),
    the commit message is read for a Co-authored-by/Owner trailer - the cheap
    attribution signal for V10 (Dina)."""
    best_ts, best, best_type, best_commit = -1, None, None, None
    events = list_paginated(gh, f"/repos/{org}/{repo}/issues/{number}/events")
    comments = list_paginated(gh, f"/repos/{org}/{repo}/issues/{number}/comments")
    for ev in events:
        ts = to_epoch(ev["created_at"]) if ev.get("created_at") else 0
        actor = (ev.get("actor") or {}).get("login")
        if actor and ts >= best_ts:
            best_ts, best = ts, actor
            best_type = (ev.get("actor") or {}).get("type")
            best_commit = ev.get("commit_id") if ev.get("event") in ("referenced", "closed", "cross-referenced") else None
    for c in comments:
        ts = to_epoch(c["created_at"]) if c.get("created_at") else 0
        actor = (c.get("user") or {}).get("login")
        if actor and ts >= best_ts:
            best_ts, best = ts, actor
            best_type, best_commit = (c.get("user") or {}).get("type"), None
    if best is None:
        best = (issue.get("user") or {}).get("login")
        best_type = (issue.get("user") or {}).get("type")
    trailer = None
    if best_commit:
        try:
            msg = ((gh.get(f"/repos/{org}/{repo}/commits/{best_commit}") or {}).get("commit") or {}).get("message") or ""
            m = TRAILER_RE.search(msg)
            if m:
                trailer = (m.group(1) or m.group(2) or "").strip() or None
        except GhError:
            trailer = None  # an unreadable commit loses the trailer, never the editor
    return best, best_type, trailer


PROJECT_FIND_Q = """query($org:String!){organization(login:$org){projectsV2(first:50){nodes{id title}}}}"""
PROJECT_ITEMS_Q = """query($id:ID!,$after:String){node(id:$id){... on ProjectV2{items(first:100,after:$after){
nodes{content{... on Issue{number repository{name}}} fieldValues(first:20){nodes{
... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}
... on ProjectV2ItemFieldDateValue{date field{... on ProjectV2FieldCommon{name}}}
... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2FieldCommon{name}}}
}}} pageInfo{hasNextPage endCursor}}}}}"""
PROJECT_TITLE = "ins-work"


MAX_PROJECT_PAGES = 50  # 5000 items is far past the org's size; a runaway cursor stops here


def project_fields(pg, org=ORG, max_pages=MAX_PROJECT_PAGES):
    """ins-work project -> ({(repo, number): {status, due}}, capped).
    Blocked-by is not a project field here: native links come from the REST
    dependencies endpoint only (Samir PR #6). Returns (None, False) when the
    project is not found. capped=True means the page cap cut the walk short."""
    data = pg.graphql(PROJECT_FIND_Q, {"org": org})
    nodes = (((data.get("data") or {}).get("organization") or {}).get("projectsV2") or {}).get("nodes") or []
    pid = None
    for n in nodes:
        if (n.get("title") or "").strip().lower() == PROJECT_TITLE:
            pid = n.get("id")
            break
    if not pid:
        return None, False
    out, after, pages = {}, None, 0
    while True:
        data = pg.graphql(PROJECT_ITEMS_Q, {"id": pid, "after": after})
        pages += 1
        items = ((data.get("data") or {}).get("node") or {}).get("items") or {}
        for node in items.get("nodes") or []:
            content = node.get("content") or {}
            repo = (content.get("repository") or {}).get("name")
            number = content.get("number")
            if not repo or number is None:
                continue  # draft items and non-issue cards carry no hygiene data
            status, due = None, None
            for fv in (node.get("fieldValues") or {}).get("nodes") or []:
                fname = ((fv.get("field") or {}).get("name") or "").strip().lower()
                if fname == "status" and fv.get("name"):
                    status = fv["name"]
                elif fname == "due" and fv.get("date"):
                    due = fv["date"]
            out[(repo, number)] = {"status": status, "due": due}
        page = items.get("pageInfo") or {}
        if not page.get("hasNextPage") or not page.get("endCursor"):
            return out, False
        if pages >= max_pages:
            return out, True
        after = page.get("endCursor")


def load_cache(path):
    """Snapshot cache {repo:number: {updated_ts, last_editor, deps}}. Anything
    unreadable is a cold cache, never an error."""
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(path, cache):
    with open(path, "w") as fh:
        json.dump(cache, fh)
    os.chmod(path, 0o600)


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
    # Project fields (Status/Due) only when the read:project token exists;
    # the snapshot stays honest without it (status None, meta says why).
    pfields, project_state, project_error, project_capped = None, "no_token", None, False
    if env.get("GH_PROJECT_TOKEN_FILE"):
        try:
            pg = Gh(read_token(env, "GH_PROJECT_TOKEN_FILE"), opener=opener)
            pfields, project_capped = project_fields(pg, org)
            project_state = "ok" if pfields is not None else "not_found"
        except GhError as e:
            pfields, project_state, project_error = None, "error", str(e)[:120]
    state_path = env.get("GH_SNAPSHOT_STATE")
    cache = load_cache(state_path) if state_path else {}
    new_cache = {}
    rows, prs, failed = [], [], []
    editor_failed = editor_cached = deps_failed = deps_cached = 0
    blocked_native = "rest"
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
        for number, (row, it) in by_number.items():
            key = "%s:%s" % (repo, number)
            ent = cache.get(key) or {}
            fresh = ent.get("updated_ts") == row["updated_ts"]
            # V10 feed: editor of last update (newest event or comment wins)
            editor_ok = False
            if fresh and "last_editor" in ent:
                row["last_editor"] = ent["last_editor"]
                row["last_editor_type"] = ent.get("last_editor_type")
                row["last_editor_trailer"] = ent.get("last_editor_trailer")
                editor_cached += 1
                editor_ok = True
            else:
                try:
                    ed, et, tr = editor_of(gh, repo, number, it, org)
                    row["last_editor"], row["last_editor_type"], row["last_editor_trailer"] = ed, et, tr
                    editor_ok = True
                except GhError:
                    editor_failed += 1
            # Project fields
            if pfields is not None:
                pf = pfields.get((repo, number))
                if pf:
                    row["status"] = pf["status"]
                    if not row["due"] and pf["due"]:
                        row["due"], row["due_source"] = pf["due"], "project"
            # Blocked-by links (REST dependencies only). The circuit breaker
            # trips on 404/422 (feature off), never on transient 5xx.
            deps_list, deps_ok = None, False
            if blocked_native == "rest":
                if fresh and "deps" in ent:
                    deps_list = ent["deps"]
                    deps_cached += 1
                    deps_ok = True
                else:
                    try:
                        deps = list_paginated(gh, f"/repos/{org}/{repo}/issues/{number}/dependencies/blocked_by")
                        deps_list = [[d["number"], (d.get("state") or "").lower()] for d in deps if d.get("number")]
                        deps_ok = True
                    except GhError as e:
                        if e.status in (404, 422):
                            blocked_native = "unsupported"  # circuit break: feature off org-wide
                        else:
                            deps_failed += 1  # transient: skip this issue, keep probing
            if deps_list:
                apply_blocked(row, [tuple(d) for d in deps_list])
            entry = {"updated_ts": row["updated_ts"]}
            if editor_ok:
                entry["last_editor"] = row["last_editor"]
                entry["last_editor_type"] = row["last_editor_type"]
                entry["last_editor_trailer"] = row["last_editor_trailer"]
            if deps_ok:
                entry["deps"] = deps_list
            if len(entry) > 1:
                new_cache[key] = entry
        # V6 support: resolve the state of referenced blockers not in the open
        # set and not already classified by native links
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
    if state_path:
        try:
            save_cache(state_path, new_cache)
        except OSError:
            pass  # a lost cache never fails the snapshot; the next run refetches
    rows.sort(key=lambda r: (r["repo"], r["number"]))
    meta = {"repos_failed": failed, "repos_ok": [r for r in repos if r not in failed],
            "issues_seen": len(rows), "prs_seen": len(prs),
            "project": project_state, "blocked_native": blocked_native,
            "editor_failed": editor_failed, "editor_cached": editor_cached,
            "deps_failed": deps_failed, "deps_cached": deps_cached,
            "project_pages_capped": project_capped}
    if project_error:
        meta["project_error"] = project_error
    return (rows, prs), meta


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    meta_path = argv[1]
    repos = argv[2:] or None
    (rows, prs), meta = snapshot(repos=repos)
    detail = (" project_error=%s" % meta["project_error"]) if meta.get("project_error") else ""
    print("gh_snapshot: project=%s blocked_native=%s editor_failed=%d deps_failed=%d repos_failed=%d%s"
          % (meta["project"], meta["blocked_native"], meta["editor_failed"],
             meta["deps_failed"], len(meta["repos_failed"]), detail), file=sys.stderr)
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
