# P4I Mode-Safe Evidence Composition

## Purpose and Boundary

PR9 human-merged P4E at b04f9a9a587a012d962a5f023558d8ff00c03aa1.
Its immutable local result prepared20 original Fed inputs, with8 eligible and12
stale at the original observation point. That was not a new live run, market
source acceptance or an accepted research report. PR and merged-main CI passed.

P4I removes an engineering constraint in pure evidence composition: today the
shared P2 functions accept only synthetic inputs. Add an explicit real-source
historical-replay composition API while preserving every legacy entrypoint,
configuration, sample and test. It is not an operational collector/importer,
real research dispatcher or publication adapter. No actual runtime store read.

All general operational flags remain false. No source selection/probe, network,
account/key/env inspection, SDK/dependency install, model, Telegram, scheduler,
broker/order/portfolio action, deployment or VPS. The old P4S probe and P4E
preparation capabilities close unconditionally under this active package.

## Engineering Interfaces

Product ownership is one writer for:
- market_intelligence/intelligence.py
- market_intelligence/market_state.py
- market_intelligence/source_policy.py (new)
- tests/product/test_real_evidence.py (new)

Keep build_bundle/verify_bundle and default validation paths synthetic-only and
byte-compatible for historical outputs. Extend shared internals explicitly;
do not temporarily relabel real data synthetic to reuse a validator. Reuse
Fact, Calendar, domain computations, news/event parsing and deterministic
recomputation. No parallel replacement engine or broad unrelated refactor.

Expose build_replay_bundle(payload, methods, entities, source_policy) and
verify_replay_bundle(bundle, payload, methods, entities, source_policy).
The new input's source mode is live_observation; processing mode is always
historical_replay. Facts retain source mode, publication, retrieval and
first-seen time and original as_of. Derive processing time from the actual UTC
clock; no public clock/as_of rewrite option. Check current retention before
semantic use and again before returning. Verification recomputes all content,
also checks current retention and cannot revive expired payload by trusting a
recorded historical processing timestamp. Clock rollback must stop processing.

A new version/schema distinguishes these bundles from historical P2 outputs.
Bind payload, methods, source policy, mapping, calendar, universe, source as_of
and processing metadata. All content is private in-memory data; no new CLI,
automatic export, persistent store or public sample containing real content.

The caller supplies an auditable specification and already obtained Facts.
The pure API cannot authenticate acquisition, legal entitlement or remote truth.
It never calls the source/preparation gate or silently reads a runtime store.
A later reviewed runtime importer must bind actual source/capture/attempt and
original rejection records before genuine research. P4I does not reinterpret
the P4S/P4E receipt, discard its rejection history or promote its source BLOCK.
Do not advertise this API alone as a complete source-to-research handoff.

## Source x Operation x Purpose

Add a strict bounded source-policy schema and pure decision helper. At minimum
identify each source, personal_local_research purpose, applicable rights states
(permitted/denied/unknown), source-located basis, retention class/duration and
origin/prefix binding. Closed fields, exact types, unique identities and limits
are mandatory; missing or contradictory required facts cannot default to permit.

Use the existing seven rights dimensions: automated_retrieval, raw_retention,
normalized_retention, derived_analysis, external_model_input, private_delivery
and public_redistribution. Each operation examines its applicable dimensions.
Composition requires normalized evidence retention and local derived analysis;
it does not retrieve or persist raw data. Unknown/denied required dimensions
block local composition. Unperformed future egress dimensions stay disabled
but do not independently block a permitted local calculation.

A rights decision is an input-specification assessment, not a runtime permission
grant or legal opinion. Never infer permission from absence of a prohibition,
a hash, API token, free price or software license. Basis URLs and state values
are auditable claims, not independently authenticated grants. No dispatch
implementation exists for any operation. No operation enables policy flags.

Bind real method sources to matching non-synthetic policy entries, exact HTTPS
origin and path prefixes, bounded authority scopes and retention. Reject
userinfo, origin confusion, encoded/path-boundary bypasses and synthetic
registries. Source tier does not prove truth or entitlement. Do not demand
model/private/public delivery rights to perform only the bounded local operation.

## Domain and Temporal Behavior

Preserve exactly000001.SH,399001.SZ,399006.SZ,KOSPI,SP500 as the benchmark target.
Methods, supplied mappings and sessions are explicit and fingerprinted; no real
production defaults or inferred holidays/constituents/sectors/providers.
Support honestly unconfigured real mappings/constituents/calendars and empty
inputs without errors that hide missingness or vacuous PASS. Return missing
benchmark coverage and unavailable/insufficient_history statistics; no zero
substitution or synthetic fallback. An empty constituent universe is unresolved,
not a successful zero-sector result. Unknown flow stays unavailable.

Reuse existing news/event/market calculations with mode-aware validation.
Require consistent source authority, symbol/provider/calendar/currency/unit,
closed sessions, publication and possession <= source as_of, and retained
payload expiry > actual processing time. Source delays/publication unknowns
must not be invented from session dates; this package selects no real provider.
Keep conflicts and rejected/stale evidence visible, enforce required BLOCK
and explicitly permitted optional DEGRADED, and never generate events from
model memory or turn no evidence into an observed empty event day.

The input-domain status may be PASS/DEGRADED/BLOCK under declared specifications.
It is not source acceptance or permission for research. Every new result retains
operational readiness BLOCKED_EXTERNAL_PREREQUISITES, permission_grant=false,
zero new live runs and live-verified coverage0/5. Even a complete authored
real-shaped test does not change these fields. Existing research, scenario,
workflow and publication code stay immutable and must reject the new schema.

## Source Research and Later Work

Independent read-only source researcher01a1e9d2-2026-7d53-a9b1-46ac1d430103
reviewed primary documentation only. No market request, SDK, account or key used.
The following findings do not select a provider or supersede historical records.

BaoStock explicitly describes saving data locally for analysis. Official index
examples establish sh.000001 and sz.399001; sz.399006 remains unverified in its
API docs. CNI identifies399006 as the ChiNext price index, not399606 total return,
but does not establish BaoStock coverage. Authenticated market transport,
dataset-specific terms and retention still need resolution. Its daily17:30
publication statement does not explicitly specify timezone. SSE trading-day
documentation is not a universal multi-market calendar.

FSC documents a free keyed HTTPS KOSPI candidate:
apis.data.go.kr/1160100/GetMarketIndexInfoService_V2/getStockMarketIndex_V2.
The detailed delay is after13:00 on the next business day, not generic realtime.
KOGL type4 and dataset-specific redistribution conditions require operation-
specific interpretation; no-modification cannot silently be read as unrestricted
normalization/derived-analysis/egress permission. A key alone is insufficient.

EODHD documents GSPC.INDX as an SP500 candidate, a free token-based daily allowance
and positive private storage/analysis language. Exact price-index provenance
versus its general indicative/CFD disclaimer, entitlement and timing remain
unverified. Do not claim the index is a CFD, change access routes to evade terms,
or confuse a one-year query window with an authorized retention period.
FRED storage restrictions remain relevant even without external model use.

Primary references:
https://www.baostock.com/mainContent?file=home.md
https://www.baostock.com/mainContent?file=indexData.md
https://www.baostock.com/mainContent?file=StockBasicInfoAPI.md
https://www.baostock.com/articleMall/api/contract?contract_status=1&contract_type=5
https://www.cnindex.com.cn/docs/gz_399006_e.pdf
https://www.data.go.kr/data/15094807/openapi.do
https://www.kogl.or.kr/info/licenseType4.do
https://eodhd.com/financial-apis/api-for-historical-data-and-volumes
https://eodhd.com/financial-apis/terms-conditions
https://fred.stlouisfed.org/legal/

No user account action is required for this pure code package. Later source
selection and actual authorized validation may require account/key setup outside
Git. Five-index0/5, model deferred, genuine run/claim sampling/private delivery,
MVP, forward observation and VPS gaps stay explicit.

## Governance and Verification

Scope172 paths,147 immutable,168 protected, exactly four current attestations.
All429 existing product cases and fixtures remain byte-identical. Preserve114
governance cases/protections; adapt active bindings and obsolete operation gates,
add tests, never delete cases to force success. Old P4E evidence is immutable.

Coordinator owns planning/state/log/receipts. Product Builder owns the four
product paths above. Governance Builder owns tools/governance.py,
tests/governance/test_governance.py and .github/workflows/governance.yml.
No overlapping writers. Actual independent entrypoint/Prompt Auditor PASS
precedes product/checker edits; full offline tests and scope/prompt/bundle/diff
precede independent Reviewer and a separate Release Checker.

Tests use authored real-shaped records ONLY, with sockets/DNS/HTTP and actual
runtime-store access blocked. Never publish those fixtures as observations.
Cover operation-specific rights, origin/path confusion, mixed modes, exact
expiry/current clock/backdated possession, numeric limits, source/mapping/
calendar conflicts, missing domains, optional degradation, mutation/rehashing,
unchanged legacy outputs and continued downstream rejection.

No operational preapproval phase or new actual-observation receipt in P4I.
Four attestation paths are bundle, validation, review and release. Final release
uses the standard LOCAL_READY_FOR_PR schema, with no pre_probe/pre_preparation
fields. Both old product authorization gates reject under P4I. Keep historical
receipt validators available as pure auditors where needed; they do not reopen
old capabilities. CI retains exact-head checkout, pinned actions, dual-platform
full suites, permissions,25-minute timeout and new baseline whitespace check.

Freeze decision-affecting bytes; one R0/R1 repair/re-review total, unknown/R2+
or second BLOCK escalates. Exact-head CI and independent remote release then
AWAITING_HUMAN_MERGE. No direct/auto merge, bypass, force push or VPS.
