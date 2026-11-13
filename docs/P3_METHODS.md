# P3 Research and Evaluation Methods

## Scope and Status

P3 is Stage5-6 offline engineering, not model-backed live research or MVP
acceptance. The five benchmarks remain 0/5 live-verified, flow unavailable and
source readiness BLOCKED_MARKET_SOURCES. P0/P1/P2 artifacts are immutable.
The demonstration uses synthetic model responses, not a hidden fallback for a
failed live provider. No credentials, product network, scheduling, publication,
Telegram, broker/order/portfolio or VPS operation is permitted.

## Verified Input and Claims

Recompute the P2 bundle using its original input, methods and entity mapping;
a valid hash alone is insufficient. Accept only synthetic_test and non-BLOCK
bundles with required domains passing and optional degradation explicitly
declared. Keep all domain gaps and confidence ceilings. Validate each selected
evidence version against bundle as_of and expiry; rejected references cannot be
cited. Do not infer that a citation proves the statement it accompanies.

Claims have bounded exact schemas, IDs, kind (fact/inference/scenario), domain,
entity, evidence IDs, informational stance, counterargument and invalidation.
An unavailable domain cannot become a substantive claim. Tier C alone cannot
confirm a fact. Validate structured output, reject unknown executable fields and
explicit trading instructions. Text checks are defense in depth, not a general
proof of semantic safety; no output is published in P3. News is untrusted data,
never a system instruction. No tool, shell, filesystem or network capabilities
are exposed to the research roles.

## Role Protocol

Researcher -> counterargument/risk reviewer -> editor is a fixed bounded
protocol. Each call gets its own context object and fixed role instruction,
model identity and prompt version. Reviewer sees exact claims and evidence,
not researcher reasoning or an assertion that they are already correct.
Each claim needs a verdict bound to its full canonical digest. At most two
research/review rounds are allowed; no review may be skipped after a failure.
The editor is called once only after all selected claims pass and may return
only approved IDs in order, not new or rewritten claim text. Unknown IDs,
duplicates, missing required verdicts and stale claim digests fail closed.

A configurable adapter interface separates deterministic synthetic responses
from unavailable live operation. Live model mode stays disabled and cannot fall
back to synthetic success. Preserve model/prompt/config/bundle fingerprints,
attempt results, rounds and reasons. Same-model role agreement is a process
check, not independent factual verification. Human entailment and live model
quality remain unassessed.

## Cost Accounting

Use a separate SQLite budget file guarded by the existing external_path
validation; never migrate or append tables to a P1 evidence database.
Store schema/version/config identity are checked before mutation. Bound disk
usage, run count and serialized records. One active run and one pending
dispatch at a time, transactionally across handles. Persistent task identity
prevents replaying a completed task or resetting a prior interrupted budget.

Reserve calls plus conservative input/output token allowances before dispatch,
per run and UTC day. Reconcile known nonnegative integer usage within the
reservation; preserve unknown or excessive usage as a blocking state, never a
silent refund. Calls, failed attempts and reservations survive reopening.
An interrupted pending reservation blocks new calls until a future explicit
recovery design; P3 does not invent provider receipts. Clock rollback and
configuration changes cannot reset accounting. Per-day budgets are tied to the
actual dispatch day, not fixture as_of. No dollar invoice claim is made.
This is one canonical budget store, not a distributed or cross-store quota
service. Creating a new store is not an authorized way to evade limits.
Source accounting remains P1-owned; delivery is absent.

Bound input/output sizes, attempts, maximum two rounds, concurrency one and
timeout handling. No hidden retries. Injected adapters are trusted test code;
a synchronous fake's elapsed timeout is not a hard cancellation guarantee for
arbitrary third-party code. No live adapter is authorized. Any later live
integration must enforce an actual transport deadline before release.

## Evaluation Definitions

Freeze these measures before publication; return numerator, denominator,
method/version and unavailable reasons rather than unsupported percentages:
citation validity, freshness, entity mapping completeness, duplicate rate,
counterargument completeness and per-domain coverage. Entailment and alert
usefulness require explicit assessment records and provenance. Missing labels
mean unassessed/null, not zero or a passing semantic check.
Sample all claims when fewer than20; otherwise deterministically select20 by
sorted stable claim IDs. Bind assessments to claim content and evaluation
version so later edits cannot reuse earlier approval. Label synthetic judgments
as synthetic; do not claim human assessment or predictive accuracy.

T+1/T+3/T+5 are descriptive outcome calculations, not a trading backtest.
Pin a mapped market and benchmark, declared anchor session/close price,
timezone-aware anchor time, explicit calendar version, price convention,
corporate-action policy, run mode and observed_at. Use actual subsequent
declared trading sessions, not calendar-day offsets. Anchor must be a completed
declared session at/before forecast as_of. Separate future observations from
the frozen forecast. Both asset and benchmark must use compatible session and
price bases. Return asset return, benchmark return and their arithmetic
difference only when inputs are valid and the horizon has matured.
Missing prices, suspensions, incomplete calendars and unsupported corporate
actions remain unavailable. Never forward-fill or call returns causality,
executable profit or forward observation. Production holiday coverage is not
claimed by supplied synthetic calendars.

## Demo and Validation

python -m market_intelligence.research demo loads fixed authored assets,
uses an ephemeral external budget store and prints raw canonical JSON only.
Separate actual run/generated timestamps from historical fixture as_of.
Commit only the actual sanitized CLI JSON, preserving numeric representations.
The sample verifies against inputs/configs and deterministic result components;
actual UUIDs, accounting timestamps and runtime duration are execution metadata.

Tests prevent sockets/DNS and cover dependency tampering, domain rejection,
role separation, injection, forbidden fields/text, stale review, editor mutation,
round bounds, budget/restart/concurrency/unknown usage/path limits, denominators,
missing assessments and mapped-session outcomes. Keep all historical tests.
One default R0/R1 correction/re-review; further BLOCK requires human decision.
Actual independent Reviewer and a distinct Release Checker approve exact
content. Later CI and human merge are separate from this frozen local record.
