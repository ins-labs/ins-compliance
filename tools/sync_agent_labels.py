"""Regenerate registry/agent-labels.txt from ins-infra's registry/agents.yaml.

Usage (run by whoever holds an org-read token, in a reviewed sync PR). The
token stays in process memory and never appears in argv (rule 7):

    GH_TOKEN_FILE=~/.tokens/org-read/password python3 - <<'EOF' \
        | python3 tools/sync_agent_labels.py
    import os, urllib.request
    req = urllib.request.Request(
        "https://api.github.com/repos/ins-labs/ins-infra/contents/registry/agents.yaml",
        headers={"Authorization": "Bearer " + open(os.environ["GH_TOKEN_FILE"]).read().strip(),
                 "Accept": "application/vnd.github.raw+json"})
    print(urllib.request.urlopen(req).read().decode())
    EOF

Reads YAML from stdin, writes the sorted unique label list. No secrets are
stored; the committed output is the only thing the CI check consumes.
"""
import re, sys

LABEL_RE = re.compile(r"^\s*-?\s*\{?\s*label:\s*([A-Za-z0-9_-]+)", re.MULTILINE)
OUT = "registry/agent-labels.txt"


def main():
    text = sys.stdin.read()
    labels = sorted(set(LABEL_RE.findall(text)))
    if not labels:
        print("no labels parsed - refusing to write an empty file", file=sys.stderr)
        return 1
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(labels) + "\n")
    print("wrote %s: %d labels" % (OUT, len(labels)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
