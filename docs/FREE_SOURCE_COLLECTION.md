# P4S Limited Source Collection

## Authorization and Scope

The user authorized a few read-only BaoStock three-index and official Fed news
tests, personal/local, zero-paid, no personal keys, only after independent review
and release. This is an upper bound, not a grant of general network permission.
All policy permission flags remain false except research_only=true.
This package further narrows actual dispatch to ONE exact Fed GET. There is no
BaoStock network implementation, SDK install or paid dependency in P4S.

The specific contract limited_probe object and frozen pre-probe approval gate
permit that single test only. Existing legacy/general probe remains disabled.
No model, Telegram, scheduler, brokerage, portfolio control, deployment or VPS.

## Source Audit (2026-11-16)

Primary sources:
- https://www.federalreserve.gov/feeds/feeds.htm
- https://www.federalreserve.gov/disclaimer.htm
- https://www.federalreserve.gov/feeds/press_all.xml
- https://www.baostock.com/mainContent?file=home.md
- https://www.baostock.com/mainContent?file=indexData.md
- https://www.baostock.com/mainContent?file=StockBasicInfoAPI.md
- https://www.baostock.com/mainContent?file=stockBasic.md
- https://www.baostock.com/disclaimer
- https://www.baostock.com/blacklist
- https://pypi.org/pypi/baostock/0.9.4/json

BaoStock's official documentation menu and markdown were read through its public
helpdocs/api/menu and helpdocs/api/markdown endpoints (JSON POST, documentation
only). The disclaimer route uses articleMall/api/contract?contract_status=1&contract_type=5,
version V1.0.0. Home documentation describes free API/local storage while the
disclaimer limits reproduction, dissemination and commercial uses of website
content. Whether those restrictions cover the API dataset has NOT been clarified.
This is a conservative unresolved rights scope, not a legal finding that private
API use is unlawful. Software BSD labeling does not establish data rights.

SDK0.9.4 metadata/wheel inspected without installation or execution. Standard
anonymous endpoint is public-api.baostock.com:10030, unencrypted TCP. No inherent
connect/read deadline, cumulative frame/decompression cap or complete-send check;
automatic pagination can issue extra requests. Source authenticity is not provided
by CRC32. Before any later use, resolve data rights and audit bounded transport,
exact index identity/type/calendar, request accounting and SDK dependency lock.
Three targets sh.000001/sz.399001/sz.399006 remain UNVERIFIED; no fallback symbol.

Fed explicitly supports RSS readers. Board content is generally public-domain
unless otherwise marked, with attribution; third-party materials/logos excluded.
Collect only press_all.xml, no linked article crawl. Existing RSS parser accepts
title/link/publication metadata; treat it as untrusted data, not instructions.
The one-day raw retention limit is our operational policy, not a claimed legal
retention requirement. No public payload/news-title redistribution in this PR.
BaoStock/Korea/US and external-model/private-delivery rights remain unresolved.

## Implementation Boundary

Keep all old source/runtime/store/model/research files and316 tests immutable.
Add market_intelligence/collection.py and tests/product/test_collection.py.
Reuse Source.authorize, exact Fed fetch, safe RSS parser, Capture and Store.
Reuse the existing read-only evidence gate, summary and freeze helpers rather
than relabeling live data synthetic or duplicating a new research implementation.

CLI commands: probe (requires --allow-network), status and sweep. No arbitrary
source/URL/store argument for live dispatch. Fixed per-user store:
Path.home() / ".a-share-market-intelligence" / "p4s-fed-probe.sqlite".
All paths use external_path protections, runtime remains outside all checkouts.
Tests may inject a temporary home/store and fake transport; production CLI
exposes neither transport nor authorization overrides.

Fixed task key p4s-fed-press-one-shot; intent includes protected snapshot.
Verify exact probe_gate before dispatch. Record a Store reservation before GET;
if ANY prior attempt exists, never send again, including failed/unknown/reserved.
Do not reset an existing store, change task intent, retry, follow redirects,
fetch articles, bypass403 or switch endpoints. No concurrency beyond Store lease.
Repeated completed task returns validated recorded summary; interrupted reservation
is visible and prevents resend. A deleted/reset store is unsupported operator
tampering, never an approved quota-renewal action.

Existing transport bounds: verified TLS, public DNS, direct connection, exact
host/path, 10-second total transport deadline, 512000-byte body, no redirects,
proxy credentials or decompression. No new dependencies.

Persist permitted raw capture and evidence with one-day expiry. Preserve the
source's original item publication time and actual retrieval/first-seen time.
Sweep uses existing tombstone logic; expiry must not erase attempts/history.
No implicit real research dispatch. Gate status and market0/5 remain blocked.

## Frozen Review and Probe Receipts

Five and only five attestation paths are excluded from protected snapshot:
p4s bundle, validation, review, release and docs/evidence/p4s-source-probe.json.
All historical receipts/source register stay immutable. Historical register0/5
is accurate for its snapshot; current source probe separately also keeps0/5.

Implement tools/governance.py probe-gate distinct from final gate. It validates
all protected bytes, exact index/worktree, audits, full offline validation,
Reviewer PASS and separate Release Checker pre-probe approval. Require local
branch, never expose --ci to product dispatch. Prompt/audit/review identities
cannot be Builders; Release Checker cannot be Reviewer.

Release receipt retains existing fields and adds pre_probe and probe_digest.
pre_probe = {verdict: LOCAL_READY_FOR_LIMITED_PROBE, snapshot_id, agent_id}.
Initial verdict LOCAL_READY_FOR_LIMITED_PROBE with probe_digest=null permits only
the first request; final LOCAL_READY_FOR_PR binds digest(canonical(probe receipt)).
Final gate rejects pending observation. Do not overwrite original approval.
Reviewer/release attestations alone are not authenticity; actual independent
agents must perform the checks.

Probe receipt exact fields: schema_version=1, package_id=P4S, snapshot_id,
status (NOT_RUN, RECORDED or BLOCKED_NO_REQUEST), observed_at, observation, reason.
Initial NOT_RUN has observed_at=null, observation=null, reason=awaiting_release.
RECORDED contains original validated P1-shaped live Fed summary, actual UTC
observed_at, reason=source_observation. Additional strict checks restrict request
count to1, exact source/live mode, known error codes, no synthetic state, no
unknown keys, exact0/5 coverage, actual observation/capture/count consistency.
BLOCKED_NO_REQUEST requires null observation, actual observed_at and a known
pre-dispatch error code. It is NOT live-verified and cannot imply coverage.
Never publish raw source body, titles, URLs other than exact known source URL,
personal paths, arbitrary exception strings or secrets.

Evidence claims are limited: successful response, parse count and domain freshness
are different outcomes. Failed HTTP/parse or stale/empty feed remains visible.
No request after pre-probe BLOCK. Final release may accept honest provider failure
as completed engineering, not accept source readiness or ignore CI/code failure.
Decision-affecting docs/state/log frozen before review; actual result only in
enumerated attestation. Later CI and human merge recorded on PR.

## Validation and Remaining Gates

Both original offline suites remain intact, plus exact-capability, one-attempt,
restart/idempotency/interruption, no implicit retry, path/store, retention,
sanitization and receipt-gate regressions. CI offline Ubuntu/Windows, exact PR
head and existing pinned actions. Timeout may25minutes for the retained suites.
One R0/R1 repair total; unknown/R2+ or second BLOCK escalates.

This package does not make the market source, live research, MVP, model/private
delivery, forward observation or VPS gates PASS. Remaining work: resolve market
source rights/transport and fixed five-index coverage, review live-to-research
integration, then actual model and private delivery integration plus genuine
dated runs and human evidence sampling. No replacement of missing data by zero.
