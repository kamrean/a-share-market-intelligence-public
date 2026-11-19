# Tencent Daily Index Replacement: Source Verification

Status: planning evidence, not implementation authorization or source approval.
Reviewed development references on 2026-11-19. No Tencent quote request has run.
This record does not change canonical permissions or any historical result.

## Request and Scope

The user requests one coherent replacement package: source verification,
adapter integration, freshness and duplicate validation, independent review,
release checking and a bounded attempt to retrieve Tencent daily indices.
BaoStock recovery is retired following the final failed diagnostic recorded in
`operations/p4d-final-connection/disposition.json`. Its journals and results stay.
Main remains merged PR13; the two subsequent diagnostic commits are local
checkpoints, not merged dependencies. The new branch is
`stage1-tencent-index-replacement`.

Proposed mainland scope is Shanghai Composite `sh000001`, Shenzhen Component
`sz399001` and ChiNext `sz399006`. Provider identity must be checked from an
actual response, not assumed from a request parameter. KOSPI and S&P500 remain
unresolved; this source decision cannot reduce the five-benchmark denominator.

## Located Technical Evidence

AKShare is an adapter maintainer, not the owner or licensor of Tencent data.
Its maintained [index documentation](https://akshare.akfamily.xyz/data/index/index.html)
documents `index_zh_daily_tx`. The inspected implementation is pinned to
[commit 738eef017571cca4fe298b272f3a65eb39d1dc12](https://github.com/akfamily/akshare/blob/738eef017571cca4fe298b272f3a65eb39d1dc12/akshare/index/index_stock_zh.py).
GitHub blob SHA is `62ecd09f6d638d5ab739b350af8af28376ab1164`; source-byte SHA256 is
`5aeda05413b73864cf49e96e489ab44fac4d9f7d75d407d3b608b7bdaf4b473a`.
Metadata and the relevant implementation were read using the GitHub API;
source bytes were not written into this repository.

The function addresses
`https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get`.
It supplies `_var` and `param` query parameters, requests daily forward-adjusted
data and handles `day` or `qfqday` arrays. The supplied implementation has no
personal credential parameter. That is not yet proof of runtime availability,
registration requirements, service limits or permitted automated access.

Calling the library directly is not suitable for the proposed narrow test:
it retrieves year-sized ranges before applying the requested date filter;
an absent start date can trigger earlier-history discovery and pagination.
Do not install the library or inherit its retry/pagination behavior merely to
perform this bounded check. A proposed narrow adapter must use a fixed HTTPS
destination, explicit dates, no redirect/retry/fallback and strict data parsing.
JSON/assignment text must never be evaluated as JavaScript or Python.

The library converts volume, amount and turnover units. Those conventions are
maintainer interpretations, not inspected upstream specifications. Initial
integration must keep these unit-dependent metrics unavailable until separately
verified. Never relabel turnover as net capital flow or mix adjustment modes.

## Usage and Retention Gap

No inspected first-party source currently establishes permission for automated
retrieval from this endpoint or for downstream analysis/retention/redistribution.
[Tencent's corporate statement](https://www.tencent.com/zh-cn/statement.html)
and the [index page](https://gu.qq.com/sh000001/zs) did not provide readable,
applicable permission terms through the inspected interfaces. Failure to locate
a term is neither a grant nor a legal conclusion that use is prohibited.

The user's request grants project-operation consent, not third-party data rights.
Public accessibility, absence of an API key, an open-source adapter license and
private/in-memory use do not independently establish the missing rights.
Normal source-policy states therefore remain unknown; no local composition,
model input, Telegram delivery or public redistribution may be promoted by this
document. Any diagnostic exception requires an explicit, reviewed policy basis;
renaming retrieval as a connectivity test must not bypass the source gate.

If an eligible run is subsequently released, payloads and intermediate values
must stay in bounded process memory and be discarded at completion. Only closed
schema counts, hashes, timestamps, error categories, quota usage and cleanup
results may persist. No payloads in Git, logs, fixtures or public CI artifacts.
Only synthetic fixtures are publishable. Old quota journals cannot be reset.

## Freshness and Duplicate Acceptance Design

The inspected [SSE calendar](https://www.sse.com.cn/disclosure/dealinstruc/closed/)
and [SZSE 2026 notice](https://www.szse.cn/disclosure/notice/t20251222_618087.html)
provide the 2026 holiday schedule. A frozen calendar must record its year and
source version; it must not extrapolate to 2027 or silently treat holidays as
weekdays. Unknown calendar coverage blocks a latest-session claim.
The [SSE trading rules](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)
and [SZSE trading rules](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)
locate the scheduled 15:00 close. These schedules cannot certify an unexpected
market closure or the provider's actual publication/finality time.

- Use timezone-aware acquisition/as-of times and Shanghai session dates.
- Exclude incomplete/future sessions. Unknown publication time, provider delay
  and finality remain unknown even when the latest expected close is present.
- Distinguish latest returned date, latest expected completed session, missing
  interior sessions, stale data and empty results. A successful HTTP response
  alone satisfies none of these checks.
- Key bars by provider, symbol, adjustment mode and session date. Identical
  duplicates are counted and collapsed deterministically; conflicting values
  for the same key are quarantined, never resolved by last-write-wins.
- Reject invalid dates, mismatched symbols, malformed/nonfinite numeric values,
  inconsistent OHLC, out-of-window rows and oversized responses.
- Replay the same in-memory input and compare deterministic identities/results.
  Offline duplicate tests do not prove that a real run encountered duplicates.

## Proposed Bounded Probe, Not Yet Enabled

Subject to a valid source-access basis, or the independently accepted limited
technical-probe exception proposed in the P4T contract, and fresh review/release:
at most one run, three fixed GET requests (one per mainland symbol), a 14-natural-
day window ending at the latest expected completed session, at most 512000
response bytes and 20 seconds per request, concurrency one, no retry or alternate
endpoint. Reserve the complete allowance durably before dispatch. An interrupted
or unknown attempt consumes it and stops further dispatch. CI never fetches.

This proposal does not enable a request. Product flags for general retrieval,
model use, Telegram, scheduling, trading and VPS remain false. The package must
preserve the historical P4D governance tests and use a fresh current approval;
old approvals cannot authorize changed bytes. A source/entry BLOCK stops work
at that gate and is reported, not converted into Reviewer or CI PASS.
