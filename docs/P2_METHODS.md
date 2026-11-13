# P2 Methods and Acceptance Boundaries

## Scope and Provenance

P2 is the offline Stage3-4 engineering foundation. Live benchmark coverage stays
0/5, source readiness BLOCKED_MARKET_SOURCES, research MVP not implemented.
Synthetic domain PASS is not live research readiness. No retrieval, models,
scheduling, delivery, trading or VPS is authorized in this package.
Existing P1 records, calendar interface and numeric/time validators are reused;
P1 runtime/storage code and historical receipts remain unchanged.

Pure input boundaries reject unknown fields, bool/nonfinite numeric values,
duplicate identities and inconsistent provenance. Pin mode, aware as_of,
published/retrieved/first_seen/expiry times, source ID/locator/tier/content hash,
parser/method/calendar/universe/mapping/profile versions. Live evidence cannot
become historical possession merely because publication preceded retrieval.
Only authored synthetic fixtures are accepted by the new demonstration CLI.
Its fixture as_of is historical; generated_at and run ID are actual execution
observations. Do not label fixture timestamps as a real observation window.
Arrays and strings are bounded; configuration is validated, not evaluated.

## Market, Sector and Flow

Keep the fixed five benchmark symbols separate from a small declared constituent
universe with versioned sector mapping. Each series pins market/calendar/currency,
provider and unit. Match explicit completed sessions; never infer holidays from
weekdays. Reject future closes and calendar/session mismatches. A real production
calendar is not integrated. Cross-currency turnover sums are prohibited.

Compute close-to-close return as current/previous - 1 using actual consecutive
declared sessions; no forward fill. Breadth counts advancing/declining/unchanged
eligible constituents and reports eligible/expected denominators. Sector return
is the declared equal-weight mean, not a capitalization-weighted exchange index.
Sector turnover share uses the observed bounded universe and consistent units,
never a claim about the entire market. Rotation ranks declared sector returns
and their change over a complete versioned lookback, with deterministic ties.
Missing lookback is insufficient_history, not a zero or fabricated percentile.
Keep observed counts/coverage and reasons beside each metric.

Flow adapter returns unavailable with provider, series_kind, value=null and
reason until a real permitted integration exists. provider_estimate,
exchange_statistic and turnover_proxy are different series kinds. Turnover is
not net flow. Optional unavailable flow degrades only under an explicit rule;
required flow blocks. No live net-flow coverage is claimed.

## News and Entities

Bound news to 100 items and events to 20 per input. Dedup groups preserve every
original ID and source locator; deterministic normalized-title/locator identity
never erases corrections or disagreements. Rank using a versioned disclosed
formula with separate authority, recency and mapped-relevance factors; scores
are priorities, not truth/profit probabilities. Stale items decay/expire from
active attention but remain referenced in audit metadata. Tier C is a lead,
never standalone confirmation. Text, including injection examples, stays data.

Entity aliases map to explicit versioned entity/sector IDs with source basis.
Unknown and multiple matches remain unresolved/ambiguous. Do not choose an
arbitrary stock or claim that a sector association establishes a price effect.

## Source-Located Event State

Implement discovered, scheduled, approaching, awaiting_confirmation, confirmed,
delayed, cancelled, expired and superseded with an explicit transition table.
Terminal states cannot silently reopen; a later correction/version is explicit.
Arrival of planned time may yield awaiting_confirmation, never confirmed.
Approaching windows and expiry are configured before evaluation.

Every event references eligible source news; unknown references block. Planned
future time is not occurred_at. A confirmed occurrence requires explicit actual
time at/before as_of and eligible non-Tier-C confirmation references observed by
as_of. Delayed/cancelled/superseded changes need source support too. Separate
event outcome, conditional expected sector impact and observed market reaction;
unknown outcome/reaction remains null. No unsupported causal or trading claim.

## Domain Gate and Frozen Output

Evaluate market, sector, flow, news, entity and event independently under a pinned
required/optional profile. Missing required validator or domain blocks globally.
Optional gaps require predeclared degradation and lower confidence. An explicit
empty-event day is valid, but a fixture does not establish live discovery.

Freeze content-addressed structured results with input/evidence references and
profile/method/calendar/mapping/universe fingerprints. Verify hash on read and
preserve rejected/gap reasons. Excluded evidence cannot enter usable references.
No duplicate raw/news body copies are retained to evade source expiry. Outputs
remain explicitly synthetic; live source integration is a later reviewed scope.
The demo prints safe bounded JSON, not a runtime database or a private path.

## Validation and Handoff

Tests prohibit sockets/DNS and cover partial/future/stale/conflicting evidence,
insufficient history, mode/temporal leakage, invalid configuration, ambiguous
mapping, injection-as-data, all event states and forbidden transitions, false
confirmation, unavailable flow, determinism and tampering. P1 transport tests
use a temporary explicit audited-policy fixture and actual P2 denies dispatch;
do not globally mock away policy validation.

One coordinator and bounded Builder own disjoint files. One default R0/R1 repair
and re-review; P1's historical exception grants no P2 repair budget. Freeze all
decision inputs before independent Reviewer and separate Release Checker. CI
and human merge are later gates, not local verdicts or research acceptance.
