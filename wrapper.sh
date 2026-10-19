#!/bin/sh
# usage: wrapper.sh RULES SHA DB CONFIG  -- runs adapter + checker together; silence never reads as clean
# run as the dedicated checker user; code dir given by CHECKER_DIR (root-owned, read-only)
umask 077
BASE=${CHECKER_DIR:-$(cd "$(dirname "$0")" && pwd)}
DIR=$(mktemp -d "${CHECKER_STATE:-$HOME/.compliance}.XXXXXX") || { echo "RULE CHECK FAILED: no temp dir (not clean)"; exit 1; }
trap 'rm -rf "$DIR"' EXIT INT TERM
python3 "$BASE/adapter.py" "$3" "$4" "$DIR/meta.json" >"$DIR/ev.jsonl" 2>/dev/null; RC1=$?
if [ $RC1 -ne 0 ]; then echo "RULE CHECK FAILED: adapter error (not clean)"; exit 1; fi
OUT=$(python3 "$BASE/checker.py" "$1" "$2" "$DIR/ev.jsonl" "$DIR/meta.json" 2>/dev/null); RC2=$?
if [ $RC2 -eq 2 ]; then echo "RULE CHECK FAILED: no events (not clean)"; exit 1; fi
if [ $RC2 -ne 0 ] || [ -z "$OUT" ]; then echo "RULE CHECK FAILED: checker error or no output (not clean)"; exit 1; fi
echo "$OUT"
