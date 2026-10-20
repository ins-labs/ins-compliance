#!/bin/sh
# usage: wrapper.sh RULES SHA DB CONFIG  -- runs adapter + checker together; silence never reads as clean
# run as the dedicated checker user; code dir given by CHECKER_DIR (root-owned, read-only)
umask 077
BASE=${CHECKER_DIR:-$(cd "$(dirname "$0")" && pwd)}
DIR=$(mktemp -d "${CHECKER_STATE:-$HOME/.compliance}.XXXXXX") || { echo "RULE CHECK FAILED: no temp dir (not clean)"; exit 1; }
trap 'rm -rf "$DIR"' EXIT INT TERM
python3 "$BASE/adapter.py" "$3" "$4" "$DIR/meta.json" >"$DIR/ev.jsonl" 2>/dev/null; RC1=$?
if [ $RC1 -ne 0 ]; then echo "RULE CHECK FAILED: adapter error (not clean)"; exit 1; fi
# Optional R17 GitHub feed (Amen PR #3): with GH_TOKEN_FILE set, snapshot the
# org's open issues and hand the checker the feed + registry. Without it R17
# reports unproven (DEGRADED) - honest, never clean.
GH_ARGS=""
if [ -n "${GH_TOKEN_FILE:-}" ]; then
  python3 "$BASE/gh_snapshot.py" "$DIR/ghmeta.json" ${GH_SNAPSHOT_REPOS:-} >"$DIR/issues.jsonl" || { echo "RULE CHECK FAILED: snapshot error (not clean)"; exit 1; }
  REG="${GH_REGISTRY:-$BASE/registry/agents.yaml}"
  [ -f "$REG" ] || { echo "RULE CHECK FAILED: registry unreadable (not clean)"; exit 1; }
  GH_ARGS="$DIR/issues.jsonl $REG"
fi
OUT=$(python3 "$BASE/checker.py" "$1" "$2" "$DIR/ev.jsonl" "$DIR/meta.json" $GH_ARGS 2>/dev/null); RC2=$?
if [ $RC2 -eq 2 ]; then echo "RULE CHECK FAILED: no events (not clean)"; exit 1; fi
if [ $RC2 -ne 0 ] || [ -z "$OUT" ]; then echo "RULE CHECK FAILED: checker error or no output (not clean)"; exit 1; fi
echo "$OUT"
