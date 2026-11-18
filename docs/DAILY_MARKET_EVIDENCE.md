# P4M Daily Market Evidence

## Purpose and Boundary

P4N PR11 is human-merged at93f6fe87c723dba32225a4895788d8edeef0b539.
Its tested implementation head is d35743371a12ae9c0f6e6d7be937fdac23408b2a.
The separate merge receipt records actual PR/main CI, not a predicted result.
The one NBS request failed with content_length and observed_bytes0, without a
capture or parse result. Several response-header checks share that error code;
the exact remote header cause is unknown. Do not infer an empty remote feed,
raise the byte limit, retry, or reset its store. Preserve historical receipts.

This medium package implements a bounded daily-response-to-domain-composition
path, verified entirely offline. It does not select or contact a provider,
install an SDK, read a private store, or implement a raw TCP protocol. A standalone
parser, another source list, or a path that rejects every legitimate input is
not sufficient. The positive test must show supplied closed-session values
reaching the existing market calculation with explicit remaining gaps.

The five benchmark target remains000001.SH,399001.SZ,399006.SZ,KOSPI,SP500.
BaoStock documents sh.000001 and sz.399001; this package's normalized daily
response adapter supports those two exact mappings. sz.399006 remains unverified,
not absent and not replaced with another index. Input coverage and actual live
verification are separate: the latter remains0/5 regardless of authored tests.

## Current Primary Evidence

On2026-11-18 the coordinator reread BaoStock's official introduction and index
documentation through their advertised public help-document API, plus the
current V1.0.0 disclaimer. This was documentation research, not a market request.

- https://www.baostock.com/mainContent?file=home.md
- https://www.baostock.com/mainContent?file=indexData.md
- https://www.baostock.com/disclaimer
- https://www.baostock.com/articleMall/api/contract?contract_status=1&contract_type=5

The introduction explicitly describes free registration-free API access and
local storage/analysis. The website disclaimer restricts copying/distribution
of website material; applicability to the API dataset remains unresolved in the
existing source audit. Do not convert that ambiguity into a blanket finding that
personal use is unlawful, nor treat a software license as a data-rights grant.
Future model processing and private/public redistribution are separate decisions.

Official daily fields include date,code,open,high,low,close,preclose,volume,amount,
pctChg. Date is a market session date, not a publication timestamp. Index volume
and amount are constituent trading aggregates, not net fund flow. The17:30 daily
update statement does not explicitly pin timezone. No publication instant or
verified finality may be manufactured from those facts. Index minute data are
not supported; success status alone can still accompany an empty result.

The prior SDK audit found unencrypted TCP at public-api.baostock.com:10030.
This package does not use it. A documentation HTTPS URL must never be presented
as the actual acquisition endpoint or proof of TLS protection. No sample obtained
over a future unverified channel is authenticated by a hash or CRC.

Independent primary-document research again did not establish a selected,
compatible anonymous KR/US source. KRX/FSC require keys; FRED storage and API
terms remain relevant; Stooq remains unresolved. This is not an exhaustive
finding that no free source exists. Those gaps do not forbid partial offline
engineering, and do not permit reducing the final MVP target.

## Product Contract

Only new daily_market.py, the required additive intelligence.py connection and
new test_daily_market.py may change product/test surfaces. Keep all618 existing
product cases and fixtures byte-identical. Preserve all legacy public behavior,
including rejection of unknown publication in the original P4I route and rejection
of real replay by synthetic research/workflow/publication. Reuse the existing
domain engine, source-policy assessments, numeric bounds, calendars and hashing.

The new pure input boundary receives bounded response bytes and explicitly
declared provenance/configuration. It must validate exact schemas, source and
request identity, byte hashes, code mapping, row counts, numbers, session dates,
possession/as_of/expiry and currently applicable local-operation rights before
using values. Bound CSV rows/columns and reject malformed, duplicate/conflicting,
out-of-scope or empty-success data without losing original denominators. No raw
provider payload is persisted or published. Tests use authored bytes only.

Keep actual transport location and documentation reference distinct, with explicit
locator roles in new provenance. Caller-supplied records, hashes, calendars and
rights remain assertions, not authenticated acquisition or entitlement. Do not
smuggle a TCP source through an HTTPS-looking identity or silently relax the
legacy HTTPS source-policy boundary. The new schema must state its limitations.

The private API is build_daily_replay_bundle(csv_bytes, declaration, methods,
entities, source_policy), with a corresponding full-recomputation verifier.
Pin CSV limits to512000 bytes,1024 data rows,10 columns and128 characters per
field. The ten-column header is exact; there is no guessed encoding or alias.
Reject an entire duplicate/conflicting code-date group; retain mutually exclusive
primary rejection counts so total=accepted+rejected. Additional diagnostics must
not inflate that denominator. Malformed structure fails; an empty result is an
explicit zero-observation gap, not a live source PASS. Benchmark turnover stays
null; index aggregate volume/amount are not a constituent turnover or net-flow proxy.

Use a closed declaration binding version/parser/source mode/as_of/calendar,
custody(source_id,run_id,request_id,first_seen_at,retrieved_at,observed_at,expires_at,
published_at=null), CSV hash and separately asserted original-response hash.
Only the received CSV hash can be recomputed; the original response is absent
and its hash is explicitly unverified. No normalization can refresh custody.

Double-layer provenance is mandatory: transport pins the supplied actual
tcp://public-api.baostock.com:10030 endpoint with tls=false and
peer_authenticated=false; no connection occurs. documentation_reference retains
the full official index-document URL and role documentation_only_not_download_origin.
The internal engine_locator is the official help-document endpoint
https://www.baostock.com/helpdocs/api/markdown/indexData.md, role
documentation_endpoint_not_acquisition_endpoint, with matching documentation
namespace /helpdocs/api/markdown/. It exists only as a compatibility reference
for the unchanged HTTPS-only engine/policy schema. It is not a downloaded-market
URL, substitute transport identity or proof of entitlement. New outputs must
carry that role wherever the projected locator appears; never expose bare
projected Facts as authenticated acquisition records. Preserve both full source
declarations and the projection in hashes/recomputation and bind them per source.
Original HTTPS/publication-required APIs keep their unchanged meanings and reject
the distinct new replay envelope. All provenance remains caller-asserted.

Unknown published_at stays null. The separate daily-observation semantics may
use a declared observation only at or after actual possession of the already
closed configured session. They cannot reconstruct what was available earlier,
infer the provider's publication time, claim finality, invent a trading calendar,
or make historical data fresh by moving its timestamps. Check actual current
time/retention before work and before returning or verifying a result, including
expiry during computation and clock rollback. No news/event publication rules
are weakened. Identity, source/series conflicts and missing calendars remain gaps.

For admitted rows require close_at <= first_seen_at <= retrieved_at <= observed_at
<= source_as_of <= actual_now using the supplied explicit calendar. Missing or
inconsistent session evidence is not repaired. publication_delay_seconds remains
unknown, acquisition_verified=false and finality_verified=false. The earliest
declared retention deadline applies even to rejected rows. Positive tests must
produce two nonempty benchmark values with input coverage2/5 while actual live
verification remains0/5 and other required domains remain blocked as appropriate.
The calendar is supplied, not independently verified. An available benchmark is
only available for the latest completed session in that declared grid; it does
not prove the grid includes the real latest exchange session. Emit explicit
calendar_completeness_verified=false and current_market_freshness=unverified.
An old session observed today must retain its old session date and this warning.

The result is a separate versioned private replay envelope with a verifier that
recomputes content and input bindings. It carries original counts and provenance,
explicit unknown temporal fields, source mode versus processing mode, input-domain
coverage versus0/5 live verification, permission_grant=false, new_live_runs=0 and
BLOCKED_EXTERNAL_PREREQUISITES. There is no CLI, file loader, collector, persistent
store, export, model dispatch or report integration in this package. Tests of the
new schema must demonstrate legacy consumers reject it, including rehashed edits.

## Review and Ownership

Governance Builder owns contract/policy/state/template/rendered prompt and the
new PR11 merge receipt, then checker/governance tests/CI after entry audits.
Product Builder owns the three product paths above after entry audits.
Coordinator owns this plan, blueprint/start/AGENTS, README, construction log and
verbatim actual attestations. No overlapping writes. Independent read-only
entrypoint/Prompt Auditor PASS must precede product or checker changes.

Close NBS, historical probe and preparation capability gates. Remove the current
P4N repair exception from active state/policy; keep its original contract/log/
receipts immutable. Keep every operational permission false and default one
R0/R1 repair/re-review. Another BLOCK, unknown finding or R2+ escalates. This
package grants no new request allowance, runtime read or source observation.

Preserve149 governance protections. Four and only four current snapshot-exempt
attestations are P4M bundle, validation, Reviewer and Release. P4N's observation,
pre-probe release and every earlier artifact become immutable history. Exact
scope, path/secret, staged/worktree, deterministic prompt and content snapshot
checks remain enforced. CI stays offline on Ubuntu and Windows with pinned actions.

Freeze protected code/state/log before full validation and independent Reviewer.
Complete full governance/product suites, scope/prompt/bundle/whitespace checks,
separate Release Checker, commit/push/PR, exact-head CI and remote release. Stop
AWAITING_HUMAN_MERGE, never direct/auto merge or bypass. No new probe is part of
this sequence. An engineering PASS cannot establish source or MVP completion.

## Progress

Earlier complete: P0-P4N engineering, evidence guards, offline research/report
workflow, bounded historical news acquisition/preparation/composition.
This package: daily observation input-to-composition engineering only.
Remaining: permitted real five-index acquisition, valid current news/event and
sector/calendar inputs, final model/report/private Telegram integration, genuine
dated runs and claim sampling/MVP acceptance; forward observation and VPS deferred.
Every final report keeps these categories separate.
