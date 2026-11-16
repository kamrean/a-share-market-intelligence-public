# Configurable Offline Workflow Before Final Integration

## Scope and Sequencing

PR #6 merged the live-integration entry, not adapters or operational permission.
The user's subsequent instruction explicitly moves model hookup to the end.
P4C implements the provider-independent offline workflow in one reviewed PR.
This supersedes the provider-first ordering in the historical P4L plan only for
this offline work. It does not waive Blueprint v1's genuine MVP evidence.

Use Python3.12 stdlib and existing domain/research/accounting/publication logic.
Do not build another agent platform or install model SDKs. Current source rights,
five-index0/5 coverage, model verification and real private delivery remain gaps.
No source/model/Telegram requests, secrets reads, scheduler, VPS or trading.

## Product Design

- A bounded, self-contained scenario contains payload, methods, entities,
  research configuration, authored role responses and publication configuration.
  All inputs are strict synthetic_test data; historical-looking dates do not
  turn it into live observation or a historical real-model run.
- Reuse P2 evidence/domain validators and P3 researcher/reviewer/editor with
  persistent conservative budgets. Accept varied scenarios, not a fixed fixture
  hash. Reject invalid evidence before research and any blocked research before
  publication. Model provider selection remains deferred with zero paid budget.
- Bind a verified execution envelope to all inputs and actual local execution
  metadata. Recompute bundle, claims, reviews, editor selection and evaluation;
  validate accounting/timestamps. An envelope hash is not a signature or proof
  of external truth. Authored role responses are never described as AI output.
- Extend P4 publication for the explicit new envelope schema only; preserve the
  original fixed-demo verification and output. Three report sections keep full
  provenance and gaps. Explanatory corrections link to the same dataset/current
  leaf; they do not permit unchecked factual changes or destructive editing.
- One offline CLI provides example export, validation, run, status, report
  export, correction and optional explicit fake-delivery exercise. Default
  behavior records locally, never sends. No dependency on a conversational AI.
- A single external workspace pins configs and persists tasks/intent before
  work, research receipts, budgets and append-only reports. Repeat completed
  tasks return saved verified output without spending again; conflicting task
  reuse fails. Interrupted/unknown/blocked work stays visible and blocks unsafe
  repeat. Status inspection must not create/reset state or refund budgets.
- Runtime inputs/output stay outside Git; bounded reads/writes, no overwrite by
  default, no linked/junction/hardlink artifacts, no raw exception/secret echo.
  Limits apply to cases, records, stores, output, calls and retries. Single-user
  local workspace, not hostile concurrent filesystem mutation or multi-store
  global quota. Durable unknown state is a safe stop, not automatic recovery.

## Ownership

Coordinator: governance documents, state/prerequisites/log/receipts, workflow.py
and test_workflow.py. Scenario Builder: scenario.py, publication.py and
test_scenario.py. Checker Builder: tools/governance.py,
tests/governance/test_governance.py and .github/workflows/governance.yml.
No overlapping writers. All prior product tests/configs/modules except the
named publication extension are immutable. Separate auditor/reviewer/release.

## Acceptance and Negative Cases

Run all original240 tests unchanged plus new varied-case end-to-end and CLI
regressions. Block sockets/DNS in product tests. Cover bad schema, duplicate keys,
limits, tampering/rehashed wrapper, future evidence, blocked domains, forbidden
claims, stale review, output/accounting changes, repeat/conflicting tasks,
restart/interruption, mismatch config, corrupt database, lineage, paths and
unknown delivery. Never invent observations, model usage or sent messages.

Final validation is followed by independent read-only Reviewer and separate
Release Checker. One in-scope repair; unknown/R2+ or second BLOCK escalates.
Freeze all decision-affecting files before review. Exact-head Linux/Windows CI
and human merge are mandatory; never direct merge, force push or bypass.

## Later Acceptance

Final integration needs separately pinned real sources/rights/calendars,
model protocol/free allowance/data-egress rules and local private Telegram
configuration. All five exact indices plus one primary news/event source,
three genuine dated runs, human claim sampling and private send/correction
must actually pass before MVP acceptance. Forward observation and VPS remain
separate later releases. Do not require a PR per source/role/evidence item.
