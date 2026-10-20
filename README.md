# ins-compliance (public repo: no verbatim orders, no findings)
LLM-free rule checker. Read-only. Rules: rules.yaml (id, check, threshold, severity, owner role), loaded only if its sha256 matches the pinned value.

## Adapter contract (events given to checker.py, JSONL)
- Fields: id (^[A-Za-z0-9_.:-]{1,80}$), ts (finite number, epoch seconds), author (user|agent|auto), topic (string, empty = no topic/root), kind (msg|card|tap|reaction|receipt), text (string).
- Events MUST be emitted sorted by ts ascending (ack_latency assumes it).
- author comes from the authenticated sender id, never from message text. Scope: the workspace group only (DMs excluded, otherwise R02 false-positives).
- Held bridge rows must not be emitted as messages. Approvals/wamids are verified against the source of record in the adapter, not from ids typed in agent text.
- Reactions: no reaction events exist yet (open todo), so approval-based rules stay manual.

## R17: github_issue_hygiene (GitHub-based task-rule measurement)

- Feed: `gh_snapshot.py META_JSON [REPO ...] > issues.jsonl` (read-only PAT from `GH_TOKEN_FILE`, root-only 400/440/600/640; exits 1 if any repo failed - a partial snapshot never reads as clean). Emits ids and metadata only, never titles/bodies.
- Run: `checker.py rules.yaml SHA events.jsonl meta.json issues.jsonl /path/to/registry/agents.yaml`. Without the issues feed R17 reports as unproven (DEGRADED), never clean.
- Model (Dina's R17_rewrite_v1 FINAL 09:22, 2026-10-20): owner = registered `agent:<name>` label, else a body `Owner: <name or id>` line resolved against the registry (labels, aliases, agent_id, retired_ids, owns_ids; hint stops at '.' or newline), else V1. A label outside the registry is V2. due = first body line `Due: YYYY-MM-DD` (Cairo date for V4); blocked = `Blocked by #n` line (blocker states resolved per repo); V5 silence windows: In progress = threshold minutes, Todo = 24 h; V9: open PRs without an issue reference; V10 report-only: last editor signature differs from the owner.
- The snapshot emits `owner_hint` (the `Owner:` line segment) per issue; no titles or bodies ever leave the feed. The feed uses the list API with pagination only, never the search API.
- Manual rules: R29 (no Docker images) and R30 (Mokalem wording) are `check: none` - listed as manual (`no_script_check`), never auto-measured.
- First run: `R17_BASELINE=1` prints a counts line (`r17_baseline`) and flags nothing; per-issue flags start after the announced grooming switch.
- The violation number (V1..V9) is carried in the flag's `count` field.
- Project-field inputs (Status/Due/Blocked by from the ins-work project) land when the read:project token exists; the snapshot then carries `status` per issue.

## Known limits
- Secret regexes catch known token shapes only; the Telegram-token pattern uses \b and misses tokens glued to word characters.
- Flags carry rule, owner, id, age_min, count only.
- Clean means: not broken in the data it sees.
