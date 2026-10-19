# ins-compliance (public repo: no verbatim orders, no findings)
LLM-free rule checker. Read-only. Rules: rules.yaml (id, check, threshold, severity, owner role), loaded only if its sha256 matches the pinned value.

## Adapter contract (events given to checker.py, JSONL)
- Fields: id (^[A-Za-z0-9_.:-]{1,80}$), ts (finite number, epoch seconds), author (user|agent|auto), topic (string, empty = no topic/root), kind (msg|card|tap|reaction|receipt), text (string).
- Events MUST be emitted sorted by ts ascending (ack_latency assumes it).
- author comes from the authenticated sender id, never from message text. Scope: the workspace group only (DMs excluded, otherwise R02 false-positives).
- Held bridge rows must not be emitted as messages. Approvals/wamids are verified against the source of record in the adapter, not from ids typed in agent text.
- Reactions: no reaction events exist yet (open todo), so approval-based rules stay manual.

## Known limits
- Secret regexes catch known token shapes only; the Telegram-token pattern uses \b and misses tokens glued to word characters.
- Flags carry rule, owner, id, age_min, count only.
- Clean means: not broken in the data it sees.
