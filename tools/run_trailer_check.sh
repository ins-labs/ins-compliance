#!/bin/bash
# run_trailer_check.sh - fail-closed merger-lane Agent-trailer check (issue #49).
#
# Usage: tools/run_trailer_check.sh owner/repo#num [clone-dir]
# Env:   GH_TOKEN_FILE      0600 token file (public repos need none; /dev/null ok)
#        INS_COMPLIANCE_PIN reviewed ins-compliance commit (default below)
#
# Advisory only: prints PASS/FAIL and exits with the checker's code. It merges
# nothing, writes no status. Every setup step is checked; the cleanup helper is
# called WITH the chosen rc on every branch and returns it verbatim (no traps -
# a RETURN trap can capture a preceding echo's status and fake a green). The
# check runs from a fresh worktree of the pinned commit (hooks disabled at
# creation; python3 -I against the stdlib-only checker). Cleanup removes only
# the worktree git created plus its own mktemp dir.
set -u

PIN="${INS_COMPLIANCE_PIN:-ffc76b488ac6c19d5b2f310fb91d70e31dd9e069}"
CLONE="${2:-ins-compliance}"

_rtc_cleanup() {
  local rc="$1"
  git -C "$CLONE" worktree remove --force "$wt" >/dev/null 2>&1
  rmdir "${wt%/*}" 2>/dev/null
  return "$rc"
}

run_trailer_check() {
  local pr="$1" wt rc
  wt="$(mktemp -d)/wt" || return 1
  git -C "$CLONE" fetch --depth 1 origin "$PIN" || { rc=$?; _rtc_cleanup "$rc"; return $?; }
  git -C "$CLONE" -c core.hooksPath=/dev/null worktree add --detach "$wt" "$PIN" || { rc=$?; _rtc_cleanup "$rc"; return $?; }
  if [ "$(git -C "$wt" rev-parse HEAD)" != "$PIN" ]; then
    echo "PIN MISMATCH - stop" >&2
    _rtc_cleanup 1
    return $?
  fi
  # No default token path: a placeholder like /dev/null is 0666 and the
  # checker's 0600 enforcement would fail before any API call. When the
  # caller set GH_TOKEN_FILE we export it; otherwise the checker runs
  # unauthenticated (public repos) and fails loudly on private ones.
  [ -n "${GH_TOKEN_FILE:-}" ] && export GH_TOKEN_FILE
  python3 -I "$wt/tools/agent_trailer_check.py" --pr "$pr"
  rc=$?
  _rtc_cleanup "$rc"
}

run_trailer_check "$1"
