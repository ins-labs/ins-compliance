#!/usr/bin/env python3
"""LLM-free compliance checker. Read-only. Flags carry fixed fields only (rule, owner, id, age_min, count);
source text is never copied into output or state. Check names come from the hard-coded CHECKS allow-list.
Usage: checker.py RULES_YAML EXPECTED_SHA256 EVENTS_JSONL
Events (normalised by the log adapter, one JSON per line): {"id","ts"(epoch s),"author"("user"|"agent"|"auto"),"topic","kind"("msg"|"card"|"tap"|"reaction"|"receipt"),"text"}
"""
import hashlib, json, re, sys, time

MAX_TEXT = 20000
SECRET = re.compile(r'(ghp_[A-Za-z0-9]{20,}|github_pat_\w{20,}|xox[bp]-[\w-]{10,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,})')
PICK = re.compile(r'^Pick:\s*(\S.*?)\s+(\d{1,3})%', re.M)
HARD_GATE = re.compile(r'(payment|pay |purchase|send as|delete|deploy|secret|key|token|\$)', re.I)
DECISION = re.compile(r'^First decision:', re.M)


def _flag(rule, owner, ident, age_min=0, count=1):
    return {"rule": rule["id"], "owner": rule["owner"], "id": str(ident)[:80], "age_min": int(age_min), "count": count}


def ack_latency_s(rule, ev, now):
    out = []
    for i, e in enumerate(ev):
        if e["author"] != "user" or e["kind"] != "msg":
            continue
        nxt = next((x for x in ev[i + 1:] if x["author"] == "agent" and x["topic"] == e["topic"] and x["kind"] == "msg"), None)
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
    return [_flag(rule, rule["owner"], e["id"], (now - e["ts"]) / 60) for e in ev
            if e["author"] == "agent" and SECRET.search(e["text"][:MAX_TEXT])]


def self_decision_pick(rule, ev, now):
    out = []
    for e in ev:
        if e["author"] != "agent" or e["kind"] != "card":
            continue
        t = e["text"][:MAX_TEXT]
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
    rules = []
    for line in raw.decode().splitlines():
        m = re.match(r'\s*- \{id: (R\d+), check: (\w+), threshold: (\d+), severity: (\w+), owner: (\w+)\}', line)
        if m:
            rules.append({"id": m[1], "check": m[2], "threshold": int(m[3]), "severity": m[4], "owner": m[5]})
    return rules


def run(rules, ev, now=None):
    now = now or time.time()
    flags, failed, measured = [], [], 0
    for r in rules:
        fn = CHECKS.get(r["check"])
        if fn is None or r["check"] in UNMEASURED:
            failed.append(r["id"]); continue
        try:
            flags += fn(r, ev, now); measured += 1
        except Exception:
            failed.append(r["id"])
    return {"checked": measured, "unproven_or_failed": failed, "flags": flags,
            "footer": "clean means: not broken in the data it sees"}


if __name__ == "__main__":
    rules = load_rules(sys.argv[1], sys.argv[2])
    ev = []
    for line in open(sys.argv[3]):
        if len(line) < 200000:
            ev.append(json.loads(line))
    print(json.dumps(run(rules, ev), ensure_ascii=False))
