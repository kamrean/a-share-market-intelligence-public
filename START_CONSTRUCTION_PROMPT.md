# A-Share Core Acceptance Entrypoint

Use only this public repository, Blueprint v1, project-state/state.json and
the active P4CORE contract. Start from human-merged #15. Keep all historic
five-index receipts and the separate local operator worktree unchanged.

The user selected free, no registration and no key: the three named A-share
indices and one permitted primary news/disclosure/official event source
are required. KOSPI and SP500 are optional context and cannot satisfy a
core slot. Implement a versioned offline evidence preflight and test actual
gate behavior. Offline samples cannot attest rights or live readiness.

No real requests, model calls, Telegram, scheduling, trading or VPS. Use
the repo's existing validations and CI with disjoint native Builders, an
independent Prompt Auditor and Reviewer, and a separate Release Checker.
Only named receipts follow the snapshot. One consolidated in-scope repair;
unknown findings or a second BLOCK escalate. Push one PR only after local
release, confirm exact-head jobs, and wait for human merge. No direct merge.
