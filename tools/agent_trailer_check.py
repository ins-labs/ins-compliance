"""Agent-trailer hygiene check (issue #46, ADR-0003).

ADVISORY HYGIENE ONLY - not a security or identity gate. It verifies that a PR
body carries exactly one distinct own-line `Agent: <label>` trailer (outside
fenced code blocks), where <label> is derived from the head branch prefix and
validated against the committed registry/agent-labels.txt. Branch names are
self-claims, not authenticated ownership; on pull_request events the checked
code and label file come from the PR itself, so a contributor can modify them
to self-pass. Treat a PASS as housekeeping signal, never as proof of
authorship. No merge, status write, or deployment is performed.

CLI:
    python3 tools/agent_trailer_check.py --event-path GITHUB_EVENT_PATH
    python3 tools/agent_trailer_check.py --body-file BODY --branch HEAD [--labels a,b | --labels-file F]
"""
import argparse, json, os, re, sys

TRAILER_RE = re.compile(r"^[ \t]*agent[ \t]*:[ \t]*([A-Za-z0-9_-]+)[ \t]*$", re.IGNORECASE | re.MULTILINE)
FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*?(?:^[ \t]*\1|\Z)", re.MULTILINE | re.DOTALL)


def _normalize_label(label):
    """Strip an optional agent: prefix so both registry forms compare equal."""
    label = str(label).strip()
    return label[6:] if label.lower().startswith("agent:") else label


def label_from_branch(branch, labels):
    """Return the registry label that prefixes `branch` (label/...), else None."""
    if not branch:
        return None
    prefix = branch.split("/", 1)[0].lower()
    for label in labels:
        if prefix == _normalize_label(label).lower():
            return _normalize_label(label)
    return None


def check_pr_body(body, label):
    """Return (ok, detail): ok iff body declares exactly one distinct agent,
    matching `label`, via an own-line trailer outside fenced code blocks."""
    if not label:
        return False, "could not derive an agent label from the head branch; use <label>/branch-name"
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    text = FENCE_RE.sub("\n", text)
    found = [m.group(1) for m in TRAILER_RE.finditer(text)]
    distinct = {f.lower() for f in found}
    if len(distinct) > 1:
        return False, "conflicting Agent trailers %s; exactly one distinct agent required" % sorted(distinct)
    if distinct and label.lower() in distinct:
        return True, "Agent: %s trailer present" % label
    if distinct:
        return False, "Agent trailer %s does not match branch label %r" % (sorted(distinct), label)
    return False, "missing own-line `Agent: %s` trailer in PR body" % label


YAML_LABEL_RE = re.compile(r"^\s*-?\s*\{?\s*label:\s*([A-Za-z0-9_-]+)", re.MULTILINE)


DEFAULT_LABELS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "registry", "agent-labels.txt")


def _load_labels(args):
    if not args.labels and not args.labels_file and os.path.exists(DEFAULT_LABELS_FILE):
        args = argparse.Namespace(labels=None, labels_file=DEFAULT_LABELS_FILE)
    if args.labels:
        return [_normalize_label(s) for s in args.labels.split(",") if s.strip()]
    if args.labels_file:
        with open(args.labels_file, encoding="utf-8") as fh:
            text = fh.read()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            found = set(YAML_LABEL_RE.findall(text))
            if found:
                return sorted(found)
            # Plain newline/comma-separated list (repo-committed sync file).
            return sorted(s for s in (l.strip() for l in re.split(r"[\n,]", text)) if s)
        if isinstance(data, dict):
            data = data.get("agents", data)
        if isinstance(data, dict):
            return sorted(_normalize_label(k) for k in data.keys())
        return [_normalize_label(a["label"] if isinstance(a, dict) else a) for a in data]
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fail CI when a PR body lacks the branch's Agent trailer")
    ap.add_argument("--body-file")
    ap.add_argument("--branch")
    ap.add_argument("--event-path", help="GitHub Actions event JSON (pull_request)")
    ap.add_argument("--labels")
    ap.add_argument("--labels-file")
    args = ap.parse_args(argv)
    labels = _load_labels(args)
    body, branch = "", args.branch
    if args.event_path:
        with open(args.event_path, encoding="utf-8") as fh:
            event = json.load(fh)
        pr = event.get("pull_request", {})
        body = pr.get("body") or ""
        branch = branch or (pr.get("head") or {}).get("ref")
    elif args.body_file:
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
    label = label_from_branch(branch, labels)
    ok, detail = check_pr_body(body, label)
    print(("PASS" if ok else "FAIL") + ": " + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
