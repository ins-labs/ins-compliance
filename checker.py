#!/usr/bin/env python3
"""LLM-free compliance checker. Read-only. Flags carry fixed fields only (rule, owner, id, age_min, count);
source text is never copied into output or state. Check names come from the hard-coded CHECKS allow-list.
Usage: checker.py RULES_YAML EXPECTED_SHA256 EVENTS_JSONL
Events (normalised by the log adapter, one JSON per line): {"id","ts"(epoch s),"author"("user"|"agent"|"auto"),"topic","kind"("msg"|"card"|"tap"|"reaction"|"receipt"),"text"}
"""
import hashlib, json, math, re, signal, sys, time

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


CHECKS = {f.__name__: f for f in (ack_latency_s, root_topic_posts, secret_pattern, self_decision_pick,
                                  one_decision_per_card, idle_unblocked_todo_min)}
UNMEASURED = {"idle_unblocked_todo_min"}


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


def run(rules, ev, now=None, input_errors=0):
    now = now or time.time()
    errs = input_errors
    try:
        ev = [e for e in ev if now - e["ts"] <= WINDOW_S]
    except Exception:
        ev, errs = [], errs + 1
    if len(ev) > MAX_EVENTS:
        ev = ev[-MAX_EVENTS:]; errs += 1
    flags, failed, manual, measured = [], [], [], 0
    for r in rules:
        if r["check"] == "none":
            manual.append(r["id"]); continue
        fn = CHECKS.get(r["check"])
        if fn is None or r["check"] in UNMEASURED:
            failed.append(r["id"]); continue
        try:
            signal.signal(signal.SIGALRM, _alarm); signal.alarm(CHECK_TIMEOUT_S)
            flags += fn(r, ev, now); measured += 1
        except Exception:
            failed.append(r["id"])
        finally:
            signal.alarm(0)
    return {"total_rules": len(rules), "checked": measured, "no_script_check": manual,
            "unproven_or_failed": failed, "input_error": errs, "flags": flags,
            "footer": "clean means: not broken in the data it sees; R07/R02 see only the workspace group; held bridge rows are scanned by R07 only (kind receipt)"}


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
    out = run(rules, ev, input_errors=bad + meta_err)
    out["events_seen"] = len(ev)
    out["skipped"] = meta_counts
    print(json.dumps(out, ensure_ascii=False))
    if len(ev) == 0:
        print("RULE CHECK FAILED: no events", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
