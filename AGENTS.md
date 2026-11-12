# Project Work Rules

Use this repository as the source of truth. Read START_CONSTRUCTION_PROMPT.md,
BLUEPRINT.md, project-state/state.json, the active contract and governance/policy.toml.
Read the active review records before resuming. Do not reconstruct state from chat.

The user authorized one coordinator plus bounded subagents. Keep one writer per
file set; independent Reviewer must be a separate read-only agent, not a Builder.
Wait for real verdicts. Checkers and CI are not independent review. Record findings
rather than prompting reviewers to pass. Follow the contract's one repair cycle.

Current P0 is governance-only. Use its exact allowed paths. All product/runtime
permissions are false; no collectors, product model calls, Telegram sends, broker,
orders, portfolio control, deployment or VPS mutation. Factual market prices and
volume are allowed research data in later contracts, not forbidden keywords.

Use apply_patch for manual edits. Preserve unrelated user changes. Before work,
inspect Git status/branch and compare expected paths with the contract. Stop on
unexpected changes, forbidden artifacts or scope expansion. Never direct-merge,
admin-bypass, push construction to main or create a remote without the recorded gate.

Use Python 3.12 and standard library only in P0. Run offline governance tests,
check, prompt --verify, bundle, and diff --check. Keep all decision-affecting files
inside the reviewed snapshot. Only four named attestation files may be attached
after review; they cannot authorize anything. Changed inputs invalidate verdicts.

Stage0-9 registry lives inside project-state/state.json; do not duplicate lifecycle
in prose. Gate output derives local readiness from current hashes and receipts.
NOT_RUN_AT_SNAPSHOT is the frozen local pre-publication observation, not live GitHub status. Read exact-head results and human merge evidence from the current public PR. No later package starts before its
contract and the prior CI/human-merge gate.

The old project is read-only, not default context. Only consult manifest-named
references when needed. No old ETF business/governance state is copied.

At interruption or context pressure, preserve a truthful checkpoint on the work
branch, name pending reviews and next gate, and resume through the entrypoint.
A fresh chat is allowed; five permanent role chats are not required.

The active target is kamrean/a-share-market-intelligence-public. The original
private foundation is provenance only; read docs/PUBLICATION_PROVENANCE.json.
Never clone or import its history, raw logs, private contacts or machine paths.
Use the verified GitHub noreply identity; privacy-scan all new history and public
surfaces before publication. Main must be protected before construction pushes.
