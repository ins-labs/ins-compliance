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
    t = "Pick: Deploy 90%\npayment"
    assert c.self_decision_pick(R["R06"], [E("c1", 0, "agent", kind="card", text=t)], N) == []
def test_pick_flag_and_unmeasurable():
    assert len(c.self_decision_pick(R["R06"], [E("c1", 0, "agent", kind="card", text="Pick: A 90%")], N)) == 1
    assert c.self_decision_pick(R["R06"], [E("c2", 0, "agent", kind="card", text="no pick")], N)[0]["note"] == "unmeasurable"
def test_two_decisions():
    assert c.one_decision_per_card(R["R01"], [E("c1", 0, "agent", kind="card", text="First decision: a\nFirst decision: b")], N)[0]["count"] == 2
def test_hash_mismatch():
    try: c.load_rules("rules.yaml", "0" * 64); assert False
    except SystemExit: pass
def test_unmeasured_never_ok():
    out = c.run(list(R.values()), [], N); assert "R17" in out["unproven_or_failed"]
