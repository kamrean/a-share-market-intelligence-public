# Grouped Live Integration and MVP Acceptance

## Current Boundary

This is the P4L governance entry, not a live adapter implementation or operational
authorization. PR #5 merged the P4 offline foundation. Its fixed synthetic
research/publication inputs cannot accept arbitrary live data by supplying keys.
Blueprint v1 and all P0-P4 product/config/test/review/sample files remain unchanged.

Keep all current external permissions false. This entry contains no request
allowlist, model provider, credential value, delivery destination or permission
to use any account. The following plan is the scope envelope for one grouped
implementation PR after prerequisites are resolved, not a request to open one
PR per source or role.

## Actual Prerequisites

The machine-readable register is
[the prerequisite snapshot](evidence/live-integration-prerequisites.json).
Its unknown account/configuration states mean not established by this review,
not that the user has no account. No credential files or environment secrets
were inspected. Documentation research is not a product request or live probe.
The five exact targets remain 000001.SH, 399001.SZ, 399006.SZ, KOSPI and SP500;
live-verified coverage remains 0/5. No ETF, futures or alternative index
substitution, forward fill, or reduced denominator is allowed.

Separate seven decisions for each proposed provider: free availability, endpoint
access/entitlement, automated retrieval, raw/normalized retention, derived local
analysis, external model processing, and private/public redistribution.
A public page or software license alone does not settle data rights.
Unknown is not an assertion of illegality. An explicit applicable grant may
suffice; a written clarification is needed where the intended use conflicts
with a restriction or remains unresolved. This is an engineering assessment,
not a legal opinion. Do not set an arbitrary expiry to invent a retention grant.

### Primary Source Findings

- Mainland candidates: [SSE legal notice](https://www.sse.com.cn/home/legal/)
  supports noncommercial browsing/downloads, not a selected automated API.
  [CNI licensing](https://www.cnindex.com.cn/zh_services/data_licensing/index.html?act_menu=4&act_service=3)
  distinguishes index quotation publication/data-service authorization.
  Manual personal downloads do not satisfy automatic live-source acceptance.
- [iFinD FAQ](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/faq.html)
  describes its free account as limited API access with a purchased terminal.
  It is not established as a standalone zero-cost provider. Preserve disabled
  paid/entitlement boundaries; no purchase or trial registration is authorized.
- [FSC KOSPI API](https://www.data.go.kr/data/15094807/openapi.do)
  is free and needs a portal key. Its detailed notes specify next-business-day
  updates after 13:00, despite generic real-time metadata. Attribution,
  noncommercial/no-modification and third-party redistribution restrictions
  need a compatible normalization/retention/model/delivery design; a key alone
  is insufficient. [KOGL Type 4](https://www.kogl.or.kr/info/licenseType4.do).
- [KRX alternative onboarding](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp)
  requires registration, key approval and service approval;
  [terms](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO005.jsp)
  also govern redistribution and post-termination use. Not selected or probed.
- [FRED terms](https://fred.stlouisfed.org/legal/) include restrictions concerning
  software/AI development and data storage/cache. Keep the SP500 candidate
  disabled for this retained-evidence architecture pending an applicable grant
  or another compatible exact-index provider. An anonymous download is not
  a permission workaround. This is not a claim that all personal viewing of
  FRED is prohibited.
- [SSE official news](https://www.sse.com.cn/aboutus/mediacenter/hotandd/)
  is a candidate for A-share primary information under its legal notice, not
  a currently approved crawler. Exclude third-party reposts and separately
  resolve automated collection, retention and external processing scope.
  The historical Federal Reserve Board press feed observation remains valid
  historical evidence only; do not confuse it with FRED or current permission.

- [BaoStock's official introduction](https://www.baostock.com/mainContent?file=home.md)
  offers free, registration-free API retrieval and local storage/analysis;
  [index documentation](https://www.baostock.com/mainContent?file=indexData.md)
  explicitly documents sh.000001 and sz.399001. Exact sz.399006 availability
  still needs an authorized identity/parse probe; it is unconfirmed, not absent.
  Standard login needs no personal key. [Access rules](https://www.baostock.com/blacklist)
  forbid concurrent connections; use much lower project limits than the stated
  ceiling. [Disclaimer](https://www.baostock.com/disclaimer) and upstream data
  rights still constrain redistribution. Local personal use is a viable
  documented candidate; external model transfer/public redistribution are
  unresolved, not expressly proven prohibited. Do not invent an account or
  arbitrary retention limit as a prerequisite for this free local path.

Provider findings are source-located summaries, not copied provider data.
Source terms and entitlements must be rechecked at the implementation freeze.
Do not claim that all possible free providers have been exhausted.

## One Implementation Work Package

Use branch stage7-live-integration for one reviewed implementation PR.
Amend the active contract/state/policy/checker together in that PR after
provider/model/private-test choices are available. No additional contract-only
PR is mandatory unless scope or authorization changes require one.
An independently audited exact contract must precede product edits.
A separate release decision must precede authorized external dispatch.

1. Pin concrete free source endpoints, HTTP methods, fields, exact symbol
   mappings, delays, session calendars, rights basis, retention and replay rules.
   Add bounded collectors/parsers with content-type/size/deadline limits,
   approved destinations, no redirects to arbitrary hosts, and no secrets in
   URLs/logs/evidence. If a provider puts keys in query parameters, redact at
   every error/log boundary and never publish complete authenticated URLs.
   Credentials remain outside Git and are read only at explicit dispatch.
2. Generalize evidence and market/news/event composition without relabeling
   historical fixtures. Existing modules needing reviewed changes include
   runtime.py, sources.py, models.py, gate.py, store.py, intelligence.py,
   market_state.py and news_events.py. Review current config schemas and
   temporal/calendaring assumptions. Enforce first_seen/retrieved <= as_of;
   latest completed sessions and source delay remain visible.
3. Generalize research.py and cost_guard.py for the selected documented model
   protocol. Keep researcher, counterargument reviewer and editor contexts
   separated, at most two rounds, no tools, exact approved-claim editing and
   reserved persistent budgets before dispatch. Choose model/prompt version,
   call/token/output/deadline/day bounds and permitted data egress explicitly.
   Unknown usage stops dispatch. Never replace a failed model run with a stub.
4. Generalize publication.py, ledger.py and delivery.py to verified live
   evidence/research. Preserve append-only lineage and original offline tests.
   Genuine transport requires hard deadlines, protocol-correct size limits,
   destination binding and durable reservation before send. Timeout/ambiguous
   result stays unknown across restart; no blind retry or exactly-once claim.
   Never mix synthetic acknowledgements with genuine send evidence.
5. Run genuine end-to-end observations only when their prerequisites and
   independent release decision are satisfied. Preserve actual windows, IDs,
   source failures, interruptions, usage and unavailable domains. Prepare
   sanitized summaries, human claim-support sampling and one private test plus
   linked correction. Publish no provider payload without redistribution rights.

These are candidate existing-file surfaces, not an allowlist for this entry.
The implementation contract enumerates exact new adapters/configuration/tests
and the final changed paths before its audit. Keep dependencies minimal; any new
library needs a pinned lock, compatible license and reproducible environment.
Do not rewrite old audit receipts or generated evidence. Extend regression
coverage rather than deleting historical behaviors to make live mode pass.

## Runtime Permission Amendment

Collection, model research and designated private test delivery are separate
conditional capabilities; none enables the others implicitly. The amendment
must bind the actual selected source/profile, model and credential alias,
local persistent store, destination alias, budgets and reviewed content hash.
Aliases are not secrets and cannot stand in for an unverified account.
Local configuration holds sensitive values outside every checkout.

No scheduler, background service, production report distribution, public channel,
broker/order/portfolio integration, deployment or VPS permission is included.
Production informational delivery and VPS require their own later release.
GitHub CI stays offline with sockets/DNS blocked for product tests.
Construction documentation browsing is not product runtime authorization.

## Validation and Genuine Acceptance

Retain the full offline suite. Add negative tests for unknown endpoints,
redirect/SSRF/oversize/malformed responses, absent or disallowed credentials,
wrong symbol/session/delay, stale/conflicting/future possession, retention
expiry, cross-mode inputs, unavailable required domains, unknown budget usage,
unsupported claims/injection/trade instructions, and delivery ambiguity/restart.
Test timeouts using bounded test transports, not slow public endpoints.

MVP acceptance is separate from engineering merge and requires:

- All five exact benchmarks plus at least one permitted primary news/disclosure
  or official event source live-verified; successful HTTP alone is insufficient.
- Three genuine dated end-to-end live research runs with unique execution and
  capture records. Record actual dates and interruptions; reprinting one run
  is not another observation. Do not fabricate days or infer unseen history.
- Deduplication, sourced upcoming events (a recorded empty day is valid),
  explicit mapping and rotation/turnover methods, real-or-unavailable flow.
- Actual bounded model research/review/editing, valid evidence links, counts and
  errors. Audit at least20 sampled claims, or all if fewer, for real semantic
  support with a human. Record sampling method, denominator and disagreements.
- A readable three-part report, authorized private Telegram test and linked
  correction with actual receipts; no real token/chat ID in public artifacts.
- Independent final Reviewer and separate Release Checker, exact-head CI and
  human merge. Real gaps remain gaps, not reasons to reinterpret PASS.

Keep research_mvp=not_implemented until the complete acceptance is evidenced.
Forward observation stays not_started and VPS not_authorized. Longer observation
and the 10-15 actual trading-day shadow VPS gate are later, separate milestones.

## Handoff and Rollback

First resolve provider/account/rights and model choices; no secret values are
needed in chat. Obtain the intended private destination through local secret
configuration before its test release. If permissions are unresolved, retain
disabled clients and stop the affected live operation, not the safety checks.
This entry does not silently authorize a less complete replacement product.

Stop/revert the proposed branch through ordinary reviewed changes if necessary;
do not reset published history, erase evidence, delete prior observations or
mutate the archived ETF repository. Stop at manual merge after this entry's
actual audits, validation, independent review, release and CI.
