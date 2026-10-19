import sys, time; sys.path.insert(0, '.')
import checker as c
R = {r["id"]: r for r in c.load_rules("rules.yaml", __import__("hashlib").sha256(open("rules.yaml","rb").read()).hexdigest())}
N = 10000
def E(i, ts, a, topic="t", kind="msg", text=""): return {"id": i, "ts": ts, "author": a, "topic": topic, "kind": kind, "text": text}
def test_ack_slow():
    ev = [E("u1", 0, "user"), E("a1", 120, "agent")]
    assert len(c.ack_latency_s(R["R05"], ev, N)) == 1
def test_ack_ok():
    assert c.ack_latency_s(R["R05"], [E("u1", 0, "user"), E("a1", 5, "agent")], N) == []
def test_root():
    assert len(c.root_topic_posts(R["R02"], [E("a1", 0, "agent", topic="")], N)) == 1
def test_secret_no_text_in_flag():
    f = c.secret_pattern(R["R07"], [E("a1", 0, "agent", text="x ghp_" + "A" * 30)], N)
    assert f and "ghp_" not in str(f)
def test_pick_gate_exempt():
    t = "First decision: x\nPick: Deploy 90%\npayment"
    assert c.self_decision_pick(R["R06"], [E("c1", 0, "agent", kind="card", text=t)], N) == []
def test_pick_flag_and_unmeasurable():
    assert len(c.self_decision_pick(R["R06"], [E("c1", 0, "agent", kind="card", text="First decision: x\nPick: A 90%")], N)) == 1
    assert c.self_decision_pick(R["R06"], [E("c2", 0, "agent", kind="card", text="First decision: x\nno pick")], N)[0]["note"] == "unmeasurable"
def test_two_decisions():
    assert c.one_decision_per_card(R["R01"], [E("c1", 0, "agent", kind="card", text="First decision: a\nFirst decision: b")], N)[0]["count"] == 2
def test_hash_mismatch():
    try: c.load_rules("rules.yaml", "0" * 64); assert False
    except SystemExit: pass
def test_unmeasured_never_ok():
    out = c.run(list(R.values()), [], N); assert "R17" in out["unproven_or_failed"]
def test_bad_id():
    f = c.root_topic_posts(R["R02"], [E("x; ignore previous", 0, "agent", topic="")], N)
    assert f[0]["id"] == "bad_id"
def test_telegram_token_and_all_authors():
    ev = [E("u1", 0, "user", text="12345678901:" + "A" * 35)]
    assert len(c.secret_pattern(R["R07"], ev, N)) == 1
def test_late_secret_found():
    ev = [E("a1", 0, "agent", text="x" * 30000 + "ghp_" + "B" * 30)]
    assert len(c.secret_pattern(R["R07"], ev, N)) == 1
def test_none_rules_counted():
    out = c.run(list(R.values()), [], N); assert out["total_rules"] == 28 and len(out["no_script_check"]) == 22
def test_parse_error_hard():
    import tempfile, hashlib
    p = tempfile.mktemp(); open(p, "w").write("rules:\n  - {id: R01, check: x}\n")
    try: c.load_rules(p, hashlib.sha256(open(p, "rb").read()).hexdigest()); assert False
    except SystemExit: pass
def test_linear_perf():
    import time as T
    ev = [E("u%d" % i, i, "user") for i in range(20000)]
    t0 = T.time(); c.ack_latency_s(R["R05"], ev, 30000); assert T.time() - t0 < 2
def test_progress_card_ignored():
    assert c.self_decision_pick(R["R06"], [E("c3", 0, "agent", kind="card", text="progress 40%")], N) == []
def test_monkey_not_gate():
    f = c.self_decision_pick(R["R06"], [E("c4", 0, "agent", kind="card", text="First decision: monkey\nPick: A 90%")], N)
    assert len(f) == 1
def test_malformed_line_counted():
    import json, tempfile, subprocess, hashlib
    p = tempfile.mktemp(); open(p, "w").write('{bad\n')
    out = subprocess.run(["python3", "checker.py", "rules.yaml", hashlib.sha256(open("rules.yaml","rb").read()).hexdigest(), p], capture_output=True, text=True).stdout
    assert json.loads(out)["input_error"] == 1
def test_bad_types_counted():
    import json, tempfile, subprocess, hashlib
    p = tempfile.mktemp(); open(p, "w").write('{"id":"a","ts":"x","author":"user","topic":"t","kind":"msg","text":""}\n{"id":"b","ts":1,"author":"root","topic":"t","kind":"msg","text":""}\n')
    out = subprocess.run(["python3", "checker.py", "rules.yaml", hashlib.sha256(open("rules.yaml","rb").read()).hexdigest(), p], capture_output=True, text=True).stdout
    assert json.loads(out)["input_error"] == 2
def test_nan_ts_counted():
    import json, tempfile, subprocess, hashlib
    p = tempfile.mktemp(); open(p, "w").write('{"id":"a","ts":NaN,"author":"agent","topic":"t","kind":"msg","text":"x"}\n')
    out = subprocess.run(["python3", "checker.py", "rules.yaml", hashlib.sha256(open("rules.yaml","rb").read()).hexdigest(), p], capture_output=True, text=True).stdout
    assert json.loads(out)["input_error"] == 1
