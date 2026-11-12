#!/bin/sh
# usage: r17_run.sh snapshot|daily  (runs as ins-compliance; GH_TOKEN_FILE root-only 0600)
umask 077
B=/opt/ins-compliance; D=/var/lib/ins-compliance
export GH_TOKEN_FILE=/etc/ins-compliance/gh-token
T=$(mktemp -d "$D/.tmp.XXXXXX") || exit 1
trap 'rm -rf "$T"' EXIT
python3 $B/gh_snapshot.py "$T/meta.json" >"$T/issues.jsonl" || { echo "R17 snapshot FAILED (previous snapshot kept)" >&2; exit 1; }
python3 -c "import json,sys;m=json.load(open('$T/meta.json'));sys.exit(1 if m.get('repos_failed') else 0)" || { echo "R17 snapshot has failed repos (kept previous)" >&2; exit 1; }
mv "$T/issues.jsonl" $D/issues.jsonl; mv "$T/meta.json" $D/ghmeta.json
date -u +%FT%TZ > $D/last_snapshot_ok
[ "$1" = daily ] || exit 0
R17_BASELINE=1 python3 $B/r17_daily.py $D/issues.jsonl $B/registry/agents.yaml > "$T/daily.json" && mv "$T/daily.json" $D/r17-daily.json && date -u +%FT%TZ > $D/last_daily_ok
