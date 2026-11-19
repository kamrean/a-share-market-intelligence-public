# P4B Bounded Mainland Market Observation

## Objective and Current Authorization

After human-merged P4M PR12, connect two documented mainland index responses to
the existing daily-market computation in one bounded engineering package. This
is a real-acquisition path, not another standalone parser or synthetic-only demo.
Do not reduce the five-index MVP target or promise that the remote test succeeds.

The user explicitly authorized isolated, version-locked official BaoStock SDK
dependencies and one anonymous, zero-paid session after independent review and
pre-probe release, with at most12 protocol requests and no retry. The user's
subsequent condition requires retaining test RESULTS ONLY and deleting all test
market data and intermediates when testing is complete. The protected
docs/evidence/p4b-user-authorization.json records that scope, not a broad grant.

Use a stricter data-minimization design: raw frames, decoded rows, CSV, identity
names, calendars and calculated values stay in bounded process memory only.
Never write them to a database, temporary file, log, Git artifact or stdout.
Compose and fully verify the replay during the same process, dispose of all
references, close the socket and terminate the CLI process. Keep only a small
sanitized attempt/result journal and the public test receipt. This is no payload
persistence, not a claim of cryptographic RAM erasure or control over OS paging.
There is no later replay after disposal; hashes retain auditability, not data.

General operational flags stay false. A new, exact limited_market_ingestion
exception authorizes this one session only; it is not inherited from P4S/P4N.
NBS/Fed and preparation gates remain closed, as do model, Telegram, scheduler,
broker, orders, portfolio control, deployment and VPS. No shutdown is authorized
for this work package. Never open or mutate the archived/private projects.

## Primary Source Audit, 2026-11-19

Documentation was fetched without a market request or SDK execution:

- https://www.baostock.com/mainContent?file=home.md
- https://www.baostock.com/mainContent?file=indexData.md
- https://www.baostock.com/mainContent?file=StockBasicInfoAPI.md
- https://www.baostock.com/mainContent?file=stockBasic.md
- https://www.baostock.com/disclaimer
- https://www.baostock.com/articleMall/api/contract?contract_status=1&contract_type=5
- https://pypi.org/pypi/baostock/0.9.4/json
- https://english.sse.com.cn/start/trading/schedule/
- https://www.szse.cn/English/services/trading/tradOverview/

The official introduction affirmatively supports registration-free API access
and local data analysis, including saving API data locally. Its website-content
disclaimer V1.0.0 restricts website copying and distribution; it does not clearly
resolve API dataset redistribution. This package relies on the specific API
description for ephemeral personal acquisition and local computation only,
not on the SDK software license as a data license. It neither certifies legal
entitlement nor declares all personal API use unlawful. External model input,
private/public data redistribution and commercial reuse remain disabled.

Official index examples identify sh.000001 as the Shanghai Composite and
sz.399001 as the Shenzhen Component. Query_stock_basic documents type2 as index
and status1 as listed; exact code/type/status must match each requested index.
Query_trade_dates reports every natural day, including closed days. Dates are
session dates, not publication times. Index volume/amount aggregate constituent
trading, not net fund flow. Daily update17:30 has no explicitly pinned timezone;
do not use it to manufacture a publication timestamp or certify final close.

SDK0.9.4 wheel SHA256:
0bf71c6069ab5890ff3596632f9c3f8f1fbc6bfcac582c2f9d6a5c11ab2cfaf8.
It uses public-api.baostock.com:10030 over unauthenticated, unencrypted TCP and
requires pandas. Static inspection found no adequate transport deadline, frame
or decompression bound, or partial-send handling, and ResultData.next may request
additional pages. The package must constrain these behaviors without replacing
the SDK's business protocol with a hand-written protocol client. Do not install
an unpinned dependency, use VIP nodes, set an API key or read user credentials.

Independent primary-document research preserves KR/US gaps. FRED explicitly
supports anonymous website downloads and identifies SP500 as the daily price
index, but website download, automated access and storage terms are distinct.
Stooq's current rules were not obtainable from its browser-verification page;
absence of a verified permission is not proof of a ban. KRX website downloads
are distinct from its keyed API and its terms restrict unauthorized automation.
No KR/US provider is selected or contacted for market data in this package.

References: https://fred.stlouisfed.org/help/data/downloading/using-the-download-data-link/;
https://fred.stlouisfed.org/series/SP500/index;
https://fred.stlouisfed.org/legal/;
https://stooq.com/db/h/;
https://data.krx.co.kr/contents/MDC/INFO/informationController/MDCINFO003.cmd.

## Dependency and Transport Boundary

Keep the base stdlib requirements.lock unchanged. A separate optional
requirements-baostock.lock pins the complete binary-wheel dependency closure
for CPython3.12 on Windows x64 and Linux x64. Install only in an isolated
environment using require-hashes and only-binary; never install globally or
execute downloaded source distributions. config/p4b_sdk_manifest.json records
the SDK wheel, dependency identities and SDK source hashes. Check the installed
SDK against the manifest before any product network dispatch; reject wrong
versions, shadowed modules, changed code or unexpected cached SDK context.
Load SDK/pandas lazily, never while importing the pure receipt validator or
running governance checks. Base offline modules retain their stdlib-only path.

Reuse official SDK login, metadata and history functions, but contain its socket
and decompression dependencies locally. Do not monkeypatch process-wide socket
or zlib modules. Restore SDK bindings in finally, prohibit concurrent SDK use,
and suppress SDK prints through a bounded/discarding sink. Never return arbitrary
provider error strings or exception text in a receipt.

Only one connection to the fixed public hostname/port is possible. Resolve once
with a bounded deadline, require public addresses, pin the chosen address for
that connection, and never fall back, reconnect or use a proxy/VIP endpoint.
The transport has a20-second total monotonic deadline, including DNS and all
messages, with socket timeouts bounded by remaining time. EOF is a failure,
partial sends are completed within the same deadline or fail, and transport
failure is sticky even if the SDK catches an exception.

Limit total received wire bytes to512000, each frame to128000 and total decoded
bytes to1048576. Bound decompression before allocation grows beyond the limit;
reject truncated, trailing, concatenated or malformed compressed streams. Record
the actual observed wire-byte count and streaming response hash, never a fake
HTTPS endpoint or proof of TLS/authenticity. Deny more than12 sends and any
unexpected/repeated SDK operation. No automatic pagination: consume only the
bounded first result page and reject results indicating more data than allowed.

The planned successful sequence is7 requests: anonymous login; one14-natural-day
calendar query ending on the prior Shanghai calendar day; exact identity query
for each of the two indices; one bounded daily query for each; anonymous logout.
The12 limit is an upper bound, not permission to retry or issue exploratory
requests. On interruption, failure or timeout, close the connection without
extra cleanup requests. Never log the anonymous login frame as a user secret.

## Capture, Composition and Data Disposal

The only live CLI is python -m market_intelligence.baostock_capture observe
--allow-network. It exposes no endpoint, credential, date-range, symbol, store,
transport, dependency-path or authorization override. A separate status command
reads sanitized results only; neither command renews a consumed allowance.

Use the existing Store's path/link guards, schema, transactions, lease, attempt
reservation and capacity checks. The fixed private journal is
Path.home() / '.a-share-market-intelligence' / 'p4b-baostock-test.sqlite'.
Reserve one whole bounded session durably before any dispatch and bind its
task/intent to this package and approved snapshot. A prior reserved, failed,
unknown, interrupted or completed attempt prevents another session. Do not delete
or reset this result journal to renew quota; it contains no market payload.
Capture/evidence/bundle payload tables must remain empty. Stage-specific result
metadata may be kept in bounded events with strict closed schemas and hashes.
Keep interrupted status and unknown usage unknown; zero is not a substitute.

The internal transport entrypoint run_once accepts a synchronous accept_response
callback. It delivers only bounded, protocol-checked response fields/data and
actual acquisition metadata, never SDK objects or sockets. Capture validates
each response immediately; rejection stops subsequent requests without logout.
The transport returns only a sanitized TransportReceipt after closing/restoring
its resources. This callback is internal composition, not a live CLI override.
Capture uses fenced Store transactions to reject any previous attempt before
reservation, retain closed-schema result events and finish with runs.summary
NULL. It must not reuse the NBS payload-persisting finish path. Renew the lease
before local computation; a lost lease leaves an honest interrupted/unknown
attempt, never permission to dispatch again.

Validate exact response method, anonymous user, echo parameters, fields, types,
row counts and date coverage. Require a unique matching listed index identity.
Require exactly one calendar row per requested natural day with0/1 flags. Do
not infer a holiday calendar from weekdays or repair missing/duplicate rows.
Ordinary auction-close time15:00 Asia/Shanghai is documented by the exchanges;
the provider-reported day grid is not an independently certified exchange
calendar. Keep that limitation explicit. Exclude today's incomplete session.

Normalize only the exact ten daily fields supported by P4M, preserving original
session dates and numeric values. Check returned dates against the queried
window/provider calendar; reject out-of-range or conflicting rows. Retain true
first response possession, retrieval and observation times. published_at and
publication delay remain unknown. An old returned session is not relabeled
current. Distinguish an observed transport response, valid rows, latest expected
provider session coverage, and full research/MVP readiness.

Keep per-response acquisition times in sanitized request metadata. The combined
P4M batch custody uses the actual completion time of the last required response,
when the whole batch became available, not the earlier response's time or a new
time invented during normalization. Name that conservative batch-possession
meaning explicitly; it does not overwrite per-response first-seen evidence.

Call the unchanged build_daily_replay_bundle and verify_daily_replay_bundle
with source-located methods/entities/policy/calendar declarations constructed
from the validated in-memory observation. Do not overwrite their conservative
provenance fields, live coverage0/5, new_live_runs0 or downstream rejection.
The separate acquisition result can report actually observed/validated symbols
and input coverage0..2/5, but not authenticated data, full five-index acceptance,
true publication/finality, completed research or newly enabled operations.
Record explicit partial/empty/failure outcomes instead of silently zero-filling.

Before returning, destroy all payload references and close the socket. Return
only whitelisted test metadata: identity/version/snapshot, run and attempt IDs,
actual times, request/byte/row counts, hashes, observed symbol IDs/session range,
composition/verification outcome and explicit limitations. No prices, volume,
amount, names, raw frames, rows, CSV, complete declarations, computed report or
exception strings. Verify journal payload tables remain empty and emit cleanup
evidence only after the checks. Sanitize failure paths identically. A later
status command cannot reconstruct or claim a retained replay from these hashes.

## Ownership, Review and Delivery

Phase1 prepares planning only: parent owns this plan, user authorization,
AGENTS/START/README/BLUEPRINT/log and PR12 merge evidence. Dependency Builder
owns the optional lock and manifest. Governance Builder owns contract/policy/
state/template/rendered prompt. Product/checker edits wait for actual merged-main
CI PASS plus independent entrypoint and Prompt Auditor PASS on frozen inputs.

After handoff, Transport Builder owns new baostock_transport.py and its new test;
Capture Builder owns new baostock_capture.py and its new test. Governance Builder
owns checker/governance tests/CI. Preserve all688 existing product tests/fixtures,
all existing product modules and all160 governance test cases/protections. No
second domain engine, raw-protocol implementation, historical receipt rewrite,
permission expansion or unrelated refactor. Base requirements remain unchanged.

Tests are offline, with authored wire-level SDK responses and real pinned SDK
parsing where applicable. They must prevent actual DNS/socket use and secrets;
exercise short sends/EOF/deadlines/DNS/address/pagination/decompression limits,
identity/calendar/field/duplicate/time conflicts, a positive two-index path
through existing computation, tampering, all terminal statuses, durable quota,
SDK restoration and zero payloads in files/database/stdout after success/failure.
Run full product/governance suites, dependency checks, scope/prompt/bundle/diff.
Both pinned, read-only CI jobs install the isolated hash-locked binary closure
before offline tests. Set the construction CI job limit to30 minutes: the
merged-main Windows baseline already took about22 minutes before this package's
dependency installation and new SDK tests. This adds build-time margin only;
the product session remains strictly20 seconds with no retry or quota increase.

Freeze protected code/state/log before independent read-only Reviewer. One R0/R1
repair/re-review total; second BLOCK, unknown or R2+ escalates. Separate Release
Checker authorizes the exact-snapshot one-shot only after validation and Review.
The market gate cannot accept --ci or historical approvals as runtime authority.
After actual observation, independent final Release verifies result and disposal;
then commit/push/one PR, exact-head dual-platform CI, remote Release and human
merge. No direct/auto merge, bypass, force push or production operation.

Exactly six current attestation paths: p4b bundle, validation, review,
pre-probe-release, final release, and docs/evidence/p4b-market-observation.json.
All older receipts, current entry audits, user authorization and PR12 merge
receipt are protected. NOT_RUN/PENDING are never PASS. A failed actual source
test is a truthful terminal observation, not automatically an engineering bug,
request-renewal permission or source acceptance.

## Progress and Next Gate

Previously merged: P0-P4M engineering, with real-source and MVP gaps. Current:
bounded two-index observation, private ephemeral computation, cleanup and actual
test results. Remaining: third mainland/KR/US source gaps, current news/events/
sector inputs, final model and private report/correction delivery, three genuine
dated research runs and claim sampling. Forward observation/VPS remain deferred.
No current receipt can mark those milestones complete.

Human merge after exact-head CI and independent remote release; resolve remaining anonymous sources and genuine model/report/private-delivery acceptance. The one-shot market allowance is consumed after any reserved attempt and never authorizes retries, scheduling or VPS.
