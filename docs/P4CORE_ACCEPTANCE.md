# A-Share Core Acceptance

The current Blueprint v1 profile is free, registration-free and keyless.
The core is 000001.SH, 399001.SZ, 399006.SZ and at least one eligible,
accountable primary news, disclosure or official event source. KOSPI and
SP500 are optional context; unavailable optional evidence is clearly
marked and does not substitute for mainland data.

```text
python -B -m market_intelligence.mvp_core preflight
```

The preflight is offline, requires no keys and has no network access.
It reports 0/3 live-verified core indices and the outstanding rights and
observation dependencies. A correct synthetic or local evidence evaluation
is not certification of a source, genuine acquisition or MVP acceptance.
The synthetic evaluator keeps the mainland evidence and calendar separate
from each optional slot's evidence and calendar. Malformed optional input
is unavailable only in that slot, never a mainland-market fault.

| Slot | Documented candidate and limit |
| --- | --- |
| Shanghai Composite | [SSE index overview](https://www.sse.com.cn/market/sseindex/overview/) establishes the index identity. The [SSE website statement](https://www.sse.com.cn/home/legal/) describes noncommercial browsing and downloading; it does not establish this application's automated retrieval, derived-use or downstream-processing permissions. |
| Shenzhen Component and ChiNext | The [index operator's service and licensing page](https://www.cnindex.com.cn/zh_services/data_licensing/index.html?act_menu=4&act_service=3) describes authorized data services and external display. Technical Tencent responses do not grant those rights. |
| Primary news | The [Federal Reserve Board RSS](https://www.federalreserve.gov/feeds/feeds.htm) and its [copyright notice](https://www.federalreserve.gov/disclaimer.htm) support a narrow Board-authored candidate: attributed title, link and release time, excluding indicated third-party materials. The historical probe is consumed; no fresh observation or automatic retrieval is enabled here. |
| KOSPI (optional) | The [KRX API instructions](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) require registration and an authentication key. Do not add this API under the current scope. |
| SP500 (optional) | The [FRED series notice](https://fred.stlouisfed.org/series/SP500) documents a third-party reproduction restriction. Public display does not allow importing it into the product. |

Prior fixtures, demos, probe receipts and validation numerators stay under
their original five-index profile. No change to historical gate semantics.

Before an actual core run, pin the selected provider, exact symbols, session
calendars, delay, retention and rights evidence per operation (retrieval,
processing, model input and private delivery considered separately). Verify
fresh capture with source and first-seen times under a separate reviewed
operational contract. Then run the real model, three dated end-to-end runs,
claim review and a private Telegram test/correction together. Do not replace
missing inputs with zeroes or count this preflight as one of those runs.
