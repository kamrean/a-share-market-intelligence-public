# P1 Source Coverage and Limitations

Documentation review: 2026-11-13. This is a bounded engineering selection, not a
blanket legal opinion. Free access, permission, authentication, implementation,
live verification and redistribution are separate dimensions. Source terms may
change; recheck before expanding use. No private keys or provider payloads belong
in this public repository.

## Fixed Benchmark Universe

| Target | Canonical symbol | Session identity | Data goal | Current live coverage |
| --- | --- | --- | --- | --- |
| Shanghai Composite | 000001.SH | Asia/Shanghai / SSE | latest completed daily session | unavailable |
| Shenzhen Component | 399001.SZ | Asia/Shanghai / SZSE | latest completed daily session | unavailable |
| ChiNext | 399006.SZ | Asia/Shanghai / SZSE | latest completed daily session | unavailable |
| KOSPI | KOSPI | Asia/Seoul / KRX | latest completed daily session | unavailable |
| S&P 500 | SP500 | America/New_York / US cash index | latest completed daily session | unavailable |

Live-verified benchmark coverage is **0/5**. These are unmet integration targets,
not five failed HTTP probes. No index request is authorized in this package.
Daily date is not a known publication time. Exchange holidays, exceptions, early
closes and provider delays must be pinned before live market validation; P1
offers an explicit-session calendar interface, not a production calendar.

Calendar references: [SSE sessions](https://english.sse.com.cn/start/trading/schedule/),
[KRX sessions](https://global.krx.co.kr/contents/GLB/06/0602/0602020204/GLB0602020204T1.jsp),
[US holidays/early closes](https://www.nasdaq.com/holidayandtradinghours).

## Primary Documentation and Selection

| Candidate | Use/access basis | Restriction or unresolved prerequisite | P1 decision |
| --- | --- | --- | --- |
| SSE Shanghai Composite | [Official legal notice](https://www.sse.com.cn/home/legal/) permits noncommercial browsing/downloads; [market-data service](https://www.sseinfo.com/services/assortment/level1/) is separately licensed | No documented free anonymous integration and retention basis selected/verified for this implementation | unavailable; no requests |
| CNI/SZSE Shenzhen Component and ChiNext | [Index licensing](https://www.cnindex.com.cn/zh_services/data_licensing/index.html?act_menu=4&act_service=3) describes external publication and data-service authorization | Public web access does not settle the intended capture, retention and redistribution scope | unavailable; no requests |
| iFinD China-index candidate | [Official example](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html) names the three exact symbols; [service guide](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center.html) documents account/free-tier conditions | No authorized key; exact free entitlement, retention and downstream-use scope not validated. Do not adopt forward-fill options from examples | documented candidate, not connected |
| Korean FSC index API | [Official dataset](https://www.data.go.kr/data/15094807/openapi.do) explicitly free, authenticated with serviceKey | Attribution/noncommercial/no-modification and third-party redistribution restrictions; no key or approved retention plan. Detailed specification says next business day after 13:00, despite generic real-time metadata | documented candidate; coverage unavailable; no requests |
| KRX OpenAPI alternative | [Onboarding](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) and [terms](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO005.jsp) require approved key/service and limit use | No credentials, service approval or resolved retention/downstream permission | disabled |
| FRED S&P 500 candidate | [Series](https://fred.stlouisfed.org/series/SP500), [API keys](https://fred.stlouisfed.org/docs/api/api_key.html), [current terms](https://fred.stlouisfed.org/legal/) | API/storage/AI-use restrictions and underlying series copyright require compatibility review; anonymous CSV is not a workaround | disabled; no requests |
| Stooq alternative | [Official site](https://stooq.com/) documentation access required browser verification during research | Automation/retention permission, delay and symbols not established | disabled; no challenge bypass |
| Federal Reserve Board press RSS | [Official RSS guidance](https://www.federalreserve.gov/feeds/feeds.htm), [disclaimer](https://www.federalreserve.gov/disclaimer.htm), [feed endpoint](https://www.federalreserve.gov/feeds/press_all.xml) | Attribute Board-authored material; exclude protected third-party material/logos; no publication-latency SLA | selected initial read-only feed; actual probe result in evidence record |

No official page is copied into a fixture. The two public test inputs are authored
synthetic fixtures. Paid-provider interface tests demonstrate a disabled boundary,
not authentication, connectivity, a subscription or a production adapter.

## Capture and Retention Basis

P1 dispatch allowlist contains only the exact HTTPS Board press feed. One local
explicit run, no scheduled service, no following article links or attachments,
no cookies/authentication, normal TLS, no redirects. Preserve each supplied
publication timestamp and offset; record actual capture/first-seen time separately.
Unknown event occurrence/effect remains unknown.

For the selected Board feed, retain raw bytes locally for at most 24 hours under
the source-specific permitted-use basis. Capture headline, source URL and
timestamps as attributed metadata; do not collect external copyrighted bodies or
insignia. Sweep removes expiring raw bytes; tombstones retain permitted provenance.
No live raw payload is committed. Expired/metadata-only captures cannot be replayed.

## Observation and Completion

The machine-readable [probe summary](evidence/p1-source-probe.json) records actual
run IDs, windows, requests/errors, content fingerprint, record counts and source
quality sample size. Documentation research and synthetic tests do not count as
live verification. The news-source result must be read from that summary, not
inferred from this matrix.

A source may be configured, implemented_unverified, live_verified, degraded,
disabled or unavailable. Permission and coverage are separate fields. A single
successful request establishes only that bounded access/parse observation.

The five benchmark gaps keep live market readiness BLOCKED_MARKET_SOURCES,
regardless of governance/tests or a news-domain PASS. Three end-to-end live
research runs, actual model research and full MVP acceptance are later gates,
not achieved by this package. Further source selection/keys/permissions require
a reviewed contract; do not relax the original five-target denominator.
