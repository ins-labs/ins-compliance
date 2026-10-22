# AGENTS.md

## Rules for coding agents
- Issue BEFORE any change: no branch, no code, no PR without an issue that states scope and acceptance checks.
- Never push to main. Work on a branch and open a PR. Merge governance per org ADR-0002 I-9: two non-author stamps, then merge only by the assigned Merger.
- Merger step (advisory, mirrors ins-infra docs/MERGE-RUNBOOK.md): before merging a PR, the merger runs the pinned Agent-trailer check once from a fresh detached worktree of the reviewed ins-compliance commit ffc76b488ac6c19d5b2f310fb91d70e31dd9e069, using the fail-closed wrapper in ins-infra docs/MERGE-RUNBOOK.md verbatim with the repo ref swapped to this repo. FAIL means fix the PR body's `Agent: <label>` trailer before merging. Merge only via the API expected-head precondition (PUT /repos/ins-labs/<repo>/pulls/<num>/merge with "sha": "<stamped head sha>"); the squash trailer names the branch author's label.
- No committed or logged secret values; secrets only via the approved secret store (the vault; org ADR-0002 I-2).
