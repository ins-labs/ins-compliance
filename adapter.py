#!/usr/bin/env python3
"""Read-only adapter: commlog.db (SQLite) -> JSONL events for checker.py.
Opens the DB read-only (URI mode=ro, query_only). Does not parse raw_json (untrusted, unmapped).
author: from `sender` matched against ID sets given in a LOCAL config file outside git (sender ids only, never text):
        {"user_senders": [...], "agent_senders": [...], "auto_senders": [...]}; unknown sender => row skipped and counted.
topic:  `conversation` (id pattern); NULL or odd => 'unknown_topic' and counted (R02 therefore cannot see root posts until the topic mapping is verified). Channels outside config group_channels are skipped and counted.
kind:   agent body with a line 'First decision:' => card; other => msg. Bridge rows (sender NULL): direction tg2wa => user, wa2tg => agent. Rows with a held_reason key, or with unparseable/oversize raw_json (held status unknown), are emitted as kind 'receipt' (scanned by R07, ignored by msg/card checks). Bridge-row author comes from the direction column: only as trustworthy as whoever can write it.
id:     source_ref if it matches the id pattern, else 'row<id>'.
Usage: adapter.py DB CONFIG_JSON META_JSON [WINDOW_DAYS] > events.jsonl  (META_JSON: skip counts + events_seen). Config: 0600, outside git, ids only.\nUnknown-sender rows are emitted as author 'auto' (R07 scans them, id only) and counted. Scope: R07/R02 see only the configured group channels.
"""
import json, re, sqlite3, sys, datetime

ID_OK = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')


MAX_ROWS = 50000
MAX_RAW = 200000


def to_epoch(s):
    s = s.strip().replace("Z", "+00:00")
    if re.search(r'[+-]\d\d$', s):
        s += ":00"
    d = datetime.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


EXPECTED_COLS = {"id", "ts", "channel", "direction", "conversation", "sender", "body", "source_ref", "raw_json"}


def check_schema(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    if not EXPECTED_COLS <= cols:
        raise SystemExit("schema mismatch: messages columns")
    n = con.execute("SELECT count(*) FROM messages").fetchone()[0]
    if n == 0:
        raise SystemExit("schema check: messages empty")


def validate_cfg(cfg):
    if not isinstance(cfg, dict):
        raise SystemExit("config invalid")
    for k in ("user_senders", "agent_senders", "auto_senders", "group_channels"):
        v = cfg.get(k)
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise SystemExit("config invalid: " + k)
    return cfg


def topic_of(conv, skipped):
    if conv is None or conv == "":
        skipped["unknown_topic"] += 1  # NULL conversation: skip+count, never read as root topic
        return "unknown_topic"
    if ID_OK.match(str(conv)):
        return str(conv)
    skipped["unknown_topic"] += 1
    return "unknown_topic"


def events(db_path, cfg, window_days=7, now=None):
    validate_cfg(cfg)
    now = now or datetime.datetime.now(datetime.timezone.utc).timestamp()
    cutoff = now - window_days * 86400
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.execute("PRAGMA query_only=ON")
    check_schema(con)
    user, agent, auto = (set(cfg.get(k, [])) for k in ("user_senders", "agent_senders", "auto_senders"))
    groups = set(cfg["group_channels"])
    bridge_dir = {"tg2wa": "user", "wa2tg": "agent"}
    out = []
    rows_seen = 0
    skipped = {"unknown_sender": 0, "outside_group": 0, "bad_ts": 0, "held": 0, "raw_unparsed": 0, "old": 0, "unknown_topic": 0}
    for rid, ts, channel, direction, conv, sender, body, ref, raw in con.execute(
            "SELECT id, ts, channel, direction, conversation, sender, body, source_ref, raw_json FROM messages "
            "ORDER BY id DESC LIMIT ?", (MAX_ROWS,)):
        rows_seen += 1
        if channel not in groups:
            skipped["outside_group"] += 1; continue
        try:
            t = to_epoch(ts)
        except Exception:
            skipped["bad_ts"] += 1; continue
        if t < cutoff:
            skipped["old"] += 1; continue
        # held rows: only the key presence is read from raw_json (untrusted); held rows are NOT dropped,
        # they are emitted as kind "receipt" so R07 still scans them while msg/card checks ignore them
        held = False  # also True when held status is unknown (unparseable/oversize raw_json)
        try:
            if raw and len(raw) <= MAX_RAW:
                j = json.loads(raw)
                if isinstance(j, dict) and "held_reason" in j:
                    held = True; skipped["held"] += 1
            elif raw:
                held = True; skipped["raw_unparsed"] += 1
        except Exception:
            held = True; skipped["raw_unparsed"] += 1
        # bridge rows have NULL sender: author from direction; other rows from sender id sets
        if sender is None and direction in bridge_dir:
            author = bridge_dir[direction]
        else:
            author = "user" if sender in user else "agent" if sender in agent else "auto" if sender in auto else None
        if author is None:
            skipped["unknown_sender"] += 1
            author = "auto"  # still emitted so R07 scans it (id only)
        body = body or ""
        kind = "receipt" if held else ("card" if author == "agent" and re.search(r'^First decision:', body, re.M) else "msg")
        out.append({"id": ref if ref and ID_OK.match(ref) else "row%d" % rid, "ts": t, "author": author,
                    "topic": topic_of(conv, skipped), "kind": kind, "text": body})
    if rows_seen >= MAX_ROWS:
        skipped["raw_unparsed"] += 1  # row cap hit: counted as input_error
    out.sort(key=lambda e: e["ts"])
    return out, skipped


if __name__ == "__main__":
    import os, stat
    if stat.S_IMODE(os.stat(sys.argv[2]).st_mode) & 0o077:
        raise SystemExit("config must be 0600")
    cfg = json.load(open(sys.argv[2]))
    ev, sk = events(sys.argv[1], cfg, int(sys.argv[4]) if len(sys.argv) > 4 else 7)
    for e in ev:
        print(json.dumps(e, ensure_ascii=False))
    json.dump({"skipped": sk, "events_seen": len(ev)}, open(sys.argv[3], "w"))
