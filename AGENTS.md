# Project Work Rules

This repository is the source of truth. Read START_CONSTRUCTION_PROMPT.md,
BLUEPRINT.md, project-state/state.json, active contract and governance/policy.toml.
Consult named design/source/review records; no default bulk read of old projects.

The user authorized one coordinator plus bounded construction agents. One writer
owns each file set. Independent Reviewer must be a separate read-only agent,
never a Builder. Wait for actual results; tests and CI cannot impersonate review.
Follow the contract's single R0/R1 repair and re-review bound. The named P1
repair_exception records the user's one additional correction/re-review for
optional-domain DEGRADED freezing only. Preserve the original BLOCK history;
do not reset consumed cycles. Another BLOCK escalates, not another automatic fix.

New work branches omit codex/ by explicit user request. Never rewrite historical
names or evidence. P0 is merged; P1 covers Stage1-2 under its own audited contract.
Do not copy old ETF code, business permissions or lifecycle state.

Only narrowly authorized local read-only retrieval is enabled. Unknown source
permission means no request. Missing keys, synthetic tests and disabled paid
adapters are not connected integrations. No scheduling, model calls, Telegram,
broker, orders, portfolio control, deployment or VPS mutation.

Use apply_patch for manual edits. Preserve unrelated user changes. Inspect Git
status/branch first; stop on unexpected changes, forbidden artifacts or scope
expansion. Do not merge, bypass protection, force-push or push construction to main.
Use the verified GitHub noreply identity and scan all public surfaces for privacy.

Python 3.12 and standard library only. Runtime SQLite/raw data stay outside the
repository. Test output belongs in temporary stores, not committed runtime files.
Run both offline test suites and every contract validation. No live network in CI.

All decision inputs, including source observations and lifecycle/log, are protected.
Only four named current attestations are hash-exempt. Historical P0 receipts and
Blueprint are immutable. Changed inputs invalidate approvals. Preserve exact
index/worktree checks, secret scanning and symlink/junction rejection.

Registry belongs in project-state/state.json. Keep engineering, source coverage,
research MVP, forward observation and VPS readiness distinct. Frozen local
NOT_RUN_AT_SNAPSHOT is not live CI; exact-head CI/merge evidence belongs on the PR.

At interruption, retain a truthful checkpoint and pending gates. A new chat can
resume through the entrypoint; no permanent five-window arrangement is required.
