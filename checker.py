#!/usr/bin/env python3
"""LLM-free compliance checker. Read-only. Flags carry fixed fields only (rule, owner, id, age_min, count);
source text is never copied into output or state. Check names come from the hard-coded CHECKS allow-list.
Usage: checker.py RULES_YAML EXPECTED_SHA256 EVENTS_JSONL
Events (normalised by the log adapter, one JSON per line): {"id","ts"(epoch s),"author"("user"|"agent"|"auto"),"topic","kind"("msg"|"card"|"tap"|"reaction"|"receipt"),"text"}
"""
import hashlib, json, math, os, re, signal, sys, time

MAX_TEXT = 20000
SECRET = re.compile(r'(ghp_[A-Za-z0-9]{20,}|github_pat_\w{20,}|xox[asrbpe]-[\w-]{10,}|xapp-[\w-]{10,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}'
                    r'|\b\d{6,12}:[A-Za-z0-9_-]{30,}\b|-----BEGIN [A-Z ]*(PRIVATE KEY|OPENSSH PRIVATE KEY)-----'
                    r'|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|\bnfp_[A-Za-z0-9]{20,}|\bhvs\.[A-Za-z0-9]{20,})')
ID_OK = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')
MAX_EVENTS = 20000
WINDOW_S = 7 * 86400
CHECK_TIMEOUT_S = 5
PICK = re.compile(r'^Pick:\s*(\S.*?)\s+(\d{1,3})%', re.M)
HARD_GATE = re.compile(r'(\bpayment\b|\bpay\b|\bpurchase\b|\bsend as\b|\bdelete\b|\bdeploy\b|\bsecrets?\b|\bkeys?\b|\btokens?\b|\$)', re.I)
DECISION = re.compile(r'^First decision:', re.M)


def _flag(rule, owner, ident, age_min=0, count=1):
    return {"rule": rule["id"], "owner": rule["owner"], "id": str(ident) if ID_OK.match(str(ident)) else "bad_id", "age_min": int(age_min), "count": count}


def ack_latency_s(rule, ev, now):
    out = []
    # single backward pass: next agent message time per topic (linear)
    nxt_by = {}
    nxt_list = [None] * len(ev)
    for i in range(len(ev) - 1, -1, -1):
        e = ev[i]
        nxt_list[i] = nxt_by.get(e["topic"])
        if e["author"] == "agent" and e["kind"] == "msg":
            nxt_by[e["topic"]] = e
    for i, e in enumerate(ev):
        if e["author"] != "user" or e["kind"] != "msg":
            continue
        nxt = nxt_list[i]
        if nxt is None:
            if now - e["ts"] > rule["threshold"]:
                out.append(_flag(rule, rule["owner"], e["id"], (now - e["ts"]) / 60))
        elif nxt["ts"] - e["ts"] > rule["threshold"]:
            out.append(_flag(rule, rule["owner"], e["id"], (nxt["ts"] - e["ts"]) / 60))
    return out


def root_topic_posts(rule, ev, now):
    return [_flag(rule, rule["owner"], e["id"], (now - e["ts"]) / 60) for e in ev
            if e["author"] == "agent" and e["kind"] in ("msg", "card") and not e.get("topic")]


def secret_pattern(rule, ev, now):
    # records message id only, never the match or context
    out = []
    for e in ev:
        t = e["text"]
        if len(t) > 400000:
            out.append(dict(_flag(rule, rule["owner"], e["id"], (now - e["ts"]) / 60), note="unscanned"))
            continue
        hit = any(SECRET.search(t[i:i + 50000 + 300]) for i in range(0, len(t), 50000))
        if hit:
            out.append(_flag(rule, rule["owner"], e["id"], (now - e["ts"]) / 60))
    return out


def self_decision_pick(rule, ev, now):
    out = []
    for e in ev:
        if e["author"] != "agent" or e["kind"] != "card":
            continue
        t = e["text"][:MAX_TEXT]
        if not DECISION.search(t):
            continue  # progress cards etc: not a decision card
        m = PICK.search(t)
        if not m:
            out.append(dict(_flag(rule, rule["owner"], e["id"], (now - e["ts"]) / 60), note="unmeasurable"))
        elif int(m.group(2)) >= rule["threshold"] and not HARD_GATE.search(t):
            out.append(_flag(rule, rule["owner"], e["id"], (now - e["ts"]) / 60))
    return out


def one_decision_per_card(rule, ev, now):
    return [_flag(rule, rule["owner"], e["id"], (now - e["ts"]) / 60, len(DECISION.findall(e["text"][:MAX_TEXT])))
            for e in ev if e["author"] == "agent" and e["kind"] == "card" and len(DECISION.findall(e["text"][:MAX_TEXT])) > 1]


def idle_unblocked_todo_min(rule, ev, now):
    return []  # needs todo feed; reported as 'unmeasured' until the adapter exists



def _cairo_today(now):
    """Today's date in Africa/Cairo (stdlib; falls back to fixed UTC+2)."""
    try:
        from zoneinfo import ZoneInfo
        import datetime
        return datetime.datetime.fromtimestamp(now, ZoneInfo("Africa/Cairo")).date()
    except Exception:
        import datetime
        return datetime.datetime.fromtimestamp(now + 7200, datetime.timezone.utc).date()


def github_issue_hygiene(rule, gh_data, now, baseline=False):
    """R17 (Dina's R17_rewrite_v1, label model). Input: gh_snapshot.py rows
    (issues + open PRs) and the agent registry label set. Flags carry the
    fixed fields only; the violation number (V1..V9) goes in count. Per-issue
    ids are repo:number, per-agent ids are the agent label. Baseline mode
    returns counts only - the first run must not flag (grooming pass first)."""
    n_min = rule["threshold"]
    issues = gh_data.get("issues", [])
    prs = gh_data.get("prs", [])
    registry = gh_data.get("registry") or set()
    # Registry: either a bare label set (legacy) or a dict with labels /
    # aliases (alias -> label) / ids (agent id, retired or owned, -> label),
    # parsed from ins-infra registry/agents.yaml (Dina FINAL 09:22).
    if isinstance(registry, dict):
        reg_labels = set(registry.get("labels") or ())
        reg_alias = dict(registry.get("aliases") or {})
        reg_ids = dict(registry.get("ids") or {})
        reg_logins = {str(k).lower(): v for k, v in (registry.get("logins") or {}).items()}
        reg_shared = {str(s).lower() for s in (registry.get("shared") or ())}
    else:
        reg_labels, reg_alias, reg_ids = set(registry), {}, {}
        reg_logins, reg_shared = {}, set()
    # Shared agent logins (Dina, V10): edits from these, from bots, or from
    # humans count as "unattributed", never as a V10 finding. The fallback set
    # applies until the registry carries shared_logins.
    if not reg_shared:
        reg_shared = {"ins-sherbeeny", "ins-agent"}
    out_flags = []
    vcount = {i: 0 for i in range(1, 11)}

    def flag(ident, v, age_min=0, note=None):
        f = _flag(rule, rule["owner"], ident, age_min, v)
        if note:
            f["note"] = note
        out_flags.append(f)
        vcount[v] += 1

    def resolve_owner(it):
        """Returns (owner_label, violation): the registered agent: label wins;
        otherwise the body Owner hint (name/id in registry); otherwise V1.
        A label outside the registry is V2."""
        agent = it.get("agent")
        if agent:
            canon = reg_alias.get(agent, agent)
            if not reg_labels or canon in reg_labels:
                return canon, None
            return None, 2
        hint = it.get("owner_hint")
        if hint:
            for tok in (t.strip(".") for t in hint.replace(",", " ").split()):
                if tok in reg_ids:
                    return reg_ids[tok], None
                canon = reg_alias.get(tok, tok)
                if canon in reg_labels:
                    return canon, None
        return None, 1

    today = _cairo_today(now)
    agent_issues = {}
    unassigned = 0
    unattributed = 0

    def resolve_token(tok):
        """label/alias/login -> registered label, or None."""
        t = reg_alias.get(tok, tok)
        t = reg_logins.get(str(t).lower(), t)
        return t if t in reg_labels else None

    for it in issues:
        ident = "%s:%s" % (it["repo"], it["number"])
        agent, v = resolve_owner(it)
        if v:
            flag(ident, v)
        if agent is None:
            unassigned += 1
        # V10 (Dina): attribution order (a) Co-authored-by/Owner trailer on the
        # newest commit reference, (b) the issue's agent: label, (c) skip.
        # V10 fires only when (a) names a different agent than the owner.
        # Without (a): shared-agent logins, bots and humans are "unattributed".
        trailer = it.get("last_editor_trailer")
        editor = it.get("last_editor")
        etype = (it.get("last_editor_type") or "").strip().lower()
        if trailer:
            named = resolve_token(trailer)
            if named and agent and named != agent:
                flag(ident, 10, note="report_only")  # V10: report, not a violation
        elif editor:
            if etype == "bot" or str(editor).lower() in reg_shared or str(editor).lower() not in reg_logins:
                unattributed += 1
        if not it.get("due"):
            flag(ident, 3)
        else:
            import datetime
            try:
                due_d = datetime.date.fromisoformat(it["due"])
                if due_d < today:
                    flag(ident, 4, (now - it["updated_ts"]) / 60)
            except ValueError:
                flag(ident, 3)  # unparseable due = no usable due
        if not it.get("blocked_line_present"):
            silent_min = (now - it["updated_ts"]) / 60
            window = n_min if (it.get("status") or "").strip().lower() == "in progress" else 24 * 60
            if silent_min > window:
                flag(ident, 5, silent_min)
        else:
            if not it.get("blocked_by"):
                flag(ident, 6)
            elif it.get("blocked_by_closed"):
                flag(ident, 6)
        if agent:
            agent_issues.setdefault(agent, []).append(it)
    if reg_labels:
        if unassigned:
            for agent in sorted(reg_labels - set(agent_issues)):
                flag(agent, 7)
        for agent, rows in agent_issues.items():
            ages = [(now - r["updated_ts"]) / 60 for r in rows]
            if ages and min(ages) > n_min:
                flag(agent, 8, min(ages))
    for pr in prs:
        if not pr.get("has_issue_ref"):
            flag("%s:%s" % (pr["repo"], pr["number"]), 9, (now - pr.get("updated_ts", now)) / 60)
    if baseline:
        counts = {"open_issues": len(issues), "open_prs": len(prs),
                  "owned": sum(1 for it in issues if resolve_owner(it)[0]),
                  "no_agent_label": sum(1 for it in issues if not it.get("agent")),
                  "no_due": sum(1 for it in issues if not it.get("due")),
                  "blocked_marked": sum(1 for it in issues if it.get("blocked_line_present")),
                  "editor_unattributed": unattributed,
                  "would_flag": {("V%d" % v): c for v, c in vcount.items() if c}}
        return {"flags": [], "counts": counts}
    return out_flags


CHECKS = {f.__name__: f for f in (ack_latency_s, root_topic_posts, secret_pattern, self_decision_pick,
                                  one_decision_per_card, idle_unblocked_todo_min, github_issue_hygiene)}
UNMEASURED = {"idle_unblocked_todo_min"}


def parse_registry(path):
    """ins-infra registry/agents.yaml -> {labels, aliases, ids} (Dina FINAL 09:22).
    labels: registered agent labels. aliases: alias -> label. ids: agent_id,
    retired_ids and owns_ids -> label, so a body "Owner: <id>" line resolves."""
    labels, aliases, ids, logins, shared = set(), {}, {}, {}, set()
    for line in open(path):
        ms = re.match(r"\s*shared_logins: \[([^\]]*)\]", line)
        if ms:
            for tok in ms.group(1).split(","):
                tok = tok.strip().lower()
                if tok:
                    shared.add(tok)
            continue
        m = re.search(r"- \{label: ([A-Za-z0-9_-]+),", line)
        if not m:
            continue
        label = m.group(1)
        labels.add(label)
        mid = re.search(r"agent_id: (agent-[A-Za-z0-9]+)", line)
        if mid:
            ids[mid.group(1)] = label
        for field in ("aliases", "retired_ids", "owns_ids", "github_logins"):
            mf = re.search(field + r": \[([^\]]*)\]", line)
            if mf:
                for tok in mf.group(1).split(","):
                    tok = tok.strip()
                    if tok:
                        if field == "aliases":
                            aliases[tok] = label
                        elif field == "github_logins":
                            logins[tok.lower()] = label
                        else:
                            ids[tok] = label
    return {"labels": labels, "aliases": aliases, "ids": ids, "logins": logins, "shared": shared}


def load_rules(path, expected_sha):
    raw = open(path, "rb").read()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise SystemExit("rules hash mismatch")
    rules, dash = [], 0
    for line in raw.decode().splitlines():
        if line.lstrip().startswith("- {"):
            dash += 1
        m = re.match(r'\s*- \{id: (R\d+), check: (\w+), threshold: (\d+), severity: (\w+), owner: (\w+)\}\s*$', line)
        if m:
            rules.append({"id": m[1], "check": m[2], "threshold": int(m[3]), "severity": m[4], "owner": m[5]})
    if len(rules) != dash:
        raise SystemExit("rules parse error: %d rule lines, %d parsed" % (dash, len(rules)))
    return rules


class _Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise _Timeout()


def run(rules, ev, now=None, input_errors=0, gh_data=None, r17_baseline=False):
    now = now or time.time()
    errs = input_errors
    try:
        ev = [e for e in ev if now - e["ts"] <= WINDOW_S]
    except Exception:
        ev, errs = [], errs + 1
    if len(ev) > MAX_EVENTS:
        ev = ev[-MAX_EVENTS:]; errs += 1
    flags, failed, manual, measured = [], [], [], 0
    r17_counts = None
    for r in rules:
        if r["check"] == "none":
            manual.append(r["id"]); continue
        fn = CHECKS.get(r["check"])
        if fn is None or r["check"] in UNMEASURED:
            failed.append(r["id"]); continue
        if r["check"] == "github_issue_hygiene":
            if gh_data is None:
                failed.append(r["id"]); continue  # unproven until the GitHub feed exists (DEGRADED, never clean)
            try:
                signal.signal(signal.SIGALRM, _alarm); signal.alarm(CHECK_TIMEOUT_S)
                if r17_baseline:
                    res = fn(r, gh_data, now, baseline=True)
                    r17_counts = res["counts"]
                else:
                    flags += fn(r, gh_data, now)
                measured += 1
            except Exception:
                failed.append(r["id"])
            finally:
                signal.alarm(0)
            continue
        try:
            signal.signal(signal.SIGALRM, _alarm); signal.alarm(CHECK_TIMEOUT_S)
            flags += fn(r, ev, now); measured += 1
        except Exception:
            failed.append(r["id"])
        finally:
            signal.alarm(0)
    out = {"total_rules": len(rules), "checked": measured, "no_script_check": manual,
            "unproven_or_failed": failed, "input_error": errs, "flags": flags,
            "footer": "clean means: not broken in the data it sees; R07/R02 see only the workspace group; held bridge rows are scanned by R07 only (kind receipt)"}
    if r17_counts is not None:
        out["r17_baseline"] = r17_counts
    return out


AUTHORS = {"user", "agent", "auto"}
KINDS = {"msg", "card", "tap", "reaction", "receipt"}


def valid_event(e):
    if not isinstance(e, dict):
        raise ValueError
    for k in ("id", "topic", "text", "author", "kind"):
        if not isinstance(e[k], str):
            raise ValueError
    if isinstance(e["ts"], bool) or not isinstance(e["ts"], (int, float)) or not math.isfinite(e["ts"]):
        raise ValueError
    if e["author"] not in AUTHORS or e["kind"] not in KINDS:
        raise ValueError
    return e


def main():
    rules = load_rules(sys.argv[1], sys.argv[2])
    ev, bad = [], 0
    meta_err = 0
    meta_counts = {}
    if len(sys.argv) > 4:
        try:
            sk = json.load(open(sys.argv[4]))["skipped"]
            meta_err = sum(int(sk.get(k, 0)) for k in ("unknown_sender", "bad_ts", "raw_unparsed", "unknown_topic"))
            meta_counts = {k: int(sk.get(k, 0)) for k in ("unknown_sender", "outside_group", "bad_ts", "held", "raw_unparsed", "unknown_topic")}
        except Exception:
            raise SystemExit("meta unreadable")
    for line in open(sys.argv[3]):
        try:
            if len(line) > 500000:
                raise ValueError
            ev.append(valid_event(json.loads(line)))
        except Exception:
            bad += 1
    gh_data = None
    if len(sys.argv) > 5:
        try:
            issues, prs = [], []
            for line in open(sys.argv[5]):
                row = json.loads(line)
                (issues if row.get("kind") == "issue" else prs).append(row)
            registry = None
            if len(sys.argv) > 6:
                try:
                    registry = parse_registry(sys.argv[6])
                    if not registry["labels"]:
                        registry = None
                except Exception:
                    registry = None
            if registry is None:
                gh_data = None  # no trustworthy registry: R17 stays unproven (DEGRADED), never clean
            else:
                gh_data = {"issues": issues, "prs": prs, "registry": registry}
        except Exception:
            raise SystemExit("gh snapshot unreadable")
    feed_meta = None
    if len(sys.argv) > 7:
        try:
            m = json.load(open(sys.argv[7]))
            feed_meta = {k: m.get(k) for k in ("project", "project_error", "blocked_native",
                                               "editor_failed", "deps_failed", "repos_failed",
                                               "project_pages_capped") if k in m}
        except Exception:
            feed_meta = {"error": "feed meta unreadable"}
    out = run(rules, ev, input_errors=bad + meta_err, gh_data=gh_data,
              r17_baseline=os.environ.get("R17_BASELINE") == "1")
    if feed_meta is not None:
        out["r17_feed"] = feed_meta
    out["events_seen"] = len(ev)
    out["skipped"] = meta_counts
    print(json.dumps(out, ensure_ascii=False))
    if len(ev) == 0:
        print("RULE CHECK FAILED: no events", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
