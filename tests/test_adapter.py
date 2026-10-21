import sys, sqlite3, tempfile, os, datetime; sys.path.insert(0, '.')
import adapter as a
def mk():
    p = tempfile.mktemp(); c = sqlite3.connect(p)
    c.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, channel TEXT NOT NULL, direction TEXT NOT NULL, peer TEXT, conversation TEXT, sender TEXT, body TEXT, source_ref TEXT UNIQUE, raw_json TEXT)")
    now = datetime.datetime.now(datetime.timezone.utc)
    def ts(m): return (now - datetime.timedelta(minutes=m)).isoformat()
    c.executemany("INSERT INTO messages(ts,channel,direction,peer,conversation,sender,body,source_ref,raw_json) VALUES(?,?,?,?,?,?,?,?,?)", [
        (ts(10), "tg", "in", None, "DM_TEST_TOPIC_1", "U1", "hi", "tg:1", "{}"),
        (ts(9), "tg", "out", None, "DM_TEST_TOPIC_1", "A1", "First decision: x\nPick: A 60%", "tg:2", "{}"),
        (ts(8), "tg", "in", None, "DM_TEST_TOPIC_1", "X9", "who", "tg:3", "{}"),
        (ts(7), "dm", "in", None, "d", "U1", "dm", "tg:4", "{}"),
        (ts(6), "tg", "out", None, "DM_TEST_TOPIC_1", "A1", "ok", "bad id!", "{}")])
    c.commit(); return p
CFG = {"user_senders": ["U1"], "agent_senders": ["A1"], "auto_senders": [], "group_channels": ["tg", "telegram"]}
def test_map():
    ev, sk = a.events(mk(), CFG)
    assert [e["author"] for e in ev] == ["user", "agent", "auto", "agent"]
    assert ev[1]["kind"] == "card" and ev[3]["id"].startswith("row")
    assert sk["unknown_sender"] == 1 and sk["outside_group"] == 1
def test_sorted():
    ev, _ = a.events(mk(), CFG); assert [e["ts"] for e in ev] == sorted(e["ts"] for e in ev)
def test_readonly():
    p = mk()
    import adapter
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
    try: con.execute("DELETE FROM messages"); assert False
    except sqlite3.OperationalError: pass

def test_bridge_direction_held_and_ts_formats():
    p = mk(); c = sqlite3.connect(p)
    now = datetime.datetime.now(datetime.timezone.utc)
    f1 = (now - datetime.timedelta(minutes=5)).isoformat()
    f2 = (now - datetime.timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M:%S.%f") + "+00"
    c.executemany("INSERT INTO messages(ts,channel,direction,peer,conversation,sender,body,source_ref,raw_json) VALUES(?,?,?,?,?,?,?,?,?)", [
        (f1, "telegram", "tg2wa", None, "topic:123456", None, "from him", "bridge_queue:1", "{}"),
        (f2, "telegram", "wa2tg", None, "topic:123456", None, "from agent", "bridge_queue:2", "{}"),
        (f2, "telegram", "wa2tg", None, "topic:123456", None, "held one", "bridge_queue:3", '{"held_reason":"x"}'),
        (f2, "telegram", "wa2tg", None, "topic:123456", None, "bad raw", "bridge_queue:4", "{nope")])
    c.commit()
    ev, sk = a.events(p, CFG)
    ids = {e["id"] for e in ev}
    assert "bridge_queue:1" in ids and "bridge_queue:2" in ids and "bridge_queue:3" in ids
    assert [e["kind"] for e in ev if e["id"] == "bridge_queue:3"] == ["receipt"]
    assert sk["held"] == 1 and sk["raw_unparsed"] == 1
    assert [e["author"] for e in ev if e["id"] in ("bridge_queue:1", "bridge_queue:2")] == ["user", "agent"]

def _run_wrapper(db, cfg, perm=0o600):
    import json, subprocess, hashlib
    cp = tempfile.mktemp(); open(cp, "w").write(json.dumps(cfg)); os.chmod(cp, perm)
    sha = hashlib.sha256(open("rules.yaml", "rb").read()).hexdigest()
    return subprocess.run(["sh", "wrapper.sh", "rules.yaml", sha, db, cp], capture_output=True, text=True)
def test_wrapper_ok_and_unknown_counted():
    import json
    r = _run_wrapper(mk(), CFG)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["events_seen"] == 4 and out["input_error"] >= 1
def test_wrapper_no_events_fails():
    r = _run_wrapper(mk(), {"user_senders": [], "agent_senders": [], "auto_senders": [], "group_channels": ["nothing"]})
    assert r.returncode == 1 and "no events" in r.stdout
def test_wrapper_bad_config_fails():
    r = _run_wrapper(mk(), {"user_senders": "x"})
    assert r.returncode == 1 and "RULE CHECK FAILED" in r.stdout
def test_config_perms():
    r = _run_wrapper(mk(), CFG, perm=0o644)
    assert r.returncode == 1
def test_unknown_sender_secret_scanned():
    p = mk(); c = sqlite3.connect(p)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    c.execute("INSERT INTO messages(ts,channel,direction,conversation,sender,body,source_ref,raw_json) VALUES(?,?,?,?,?,?,?,?)", (now, "tg", "in", "DM_TEST_TOPIC_1", "ZZ", "ghp_" + "C" * 30, "tg:77", "{}"))
    c.commit()
    ev, sk = a.events(p, CFG)
    import checker
    f = checker.secret_pattern({"id": "R07", "owner": "all", "threshold": 0}, ev, __import__("time").time())
    assert [x["id"] for x in f] == ["tg:77"] and "ghp_" not in str(f)

def test_null_conversation_unknown_topic_and_epoch_ts_not_dropped():
    p = mk(); c = sqlite3.connect(p)
    now = datetime.datetime.now(datetime.timezone.utc)
    c.execute("INSERT INTO messages(ts,channel,direction,conversation,sender,body,source_ref,raw_json) VALUES(?,?,?,?,?,?,?,?)", (now.isoformat(), "tg", "out", None, "A1", "x", "bridge_queue:9", "{}"))
    c.commit()
    ev, sk = a.events(p, CFG)
    e = [x for x in ev if x["id"] == "bridge_queue:9"][0]
    assert e["topic"] == "unknown_topic" and sk["unknown_topic"] >= 1
def test_schema_gate():
    p = tempfile.mktemp(); c = sqlite3.connect(p); c.execute("CREATE TABLE messages(id INTEGER)"); c.commit()
    try: a.events(p, CFG); assert False
    except SystemExit: pass

def test_held_secret_still_scanned():
    p = mk(); c = sqlite3.connect(p)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    c.execute("INSERT INTO messages(ts,channel,direction,conversation,sender,body,source_ref,raw_json) VALUES(?,?,?,?,?,?,?,?)", (now, "telegram", "wa2tg", "topic:123456", None, "ghp_" + "D" * 30, "bridge_queue:55", '{"held_reason":"x"}'))
    c.commit()
    ev, _ = a.events(p, CFG)
    import checker, time
    f = checker.secret_pattern({"id": "R07", "owner": "all", "threshold": 0}, ev, time.time())
    assert [x["id"] for x in f] == ["bridge_queue:55"]
def test_wrapper_uses_private_dir_and_cleans():
    import glob, json
    base = tempfile.mkdtemp(); os.environ["CHECKER_STATE"] = base + "/st"
    r = _run_wrapper(mk(), CFG)
    assert r.returncode == 0 and glob.glob(base + "/st.*") == []

def test_unparsed_raw_is_receipt():
    p = mk(); c = sqlite3.connect(p)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    c.execute("INSERT INTO messages(ts,channel,direction,conversation,sender,body,source_ref,raw_json) VALUES(?,?,?,?,?,?,?,?)", (now, "telegram", "wa2tg", "topic:123456", None, "x", "bridge_queue:66", "{nope"))
    c.commit()
    ev, sk = a.events(p, CFG)
    assert [e["kind"] for e in ev if e["id"] == "bridge_queue:66"] == ["receipt"] and sk["raw_unparsed"] >= 1
