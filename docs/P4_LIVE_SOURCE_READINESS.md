# Live MVP source selection checkpoint

Documentation-only review, 2026-11-21. PR #16 was human-merged at
`39f89d78987cca0f34d80dea8072451100b44ce5`, with [merged-main CI
PASS](https://github.com/kamrean/a-share-market-intelligence-public/actions/runs/44890678839).
This is an input to the grouped implementation contract, not a source
selection, runtime authorization, reissued probe allowance, or MVP result.
No product endpoint was requested during this documentation review. Any
subsequent P4FED observation belongs only to its [result](evidence/p4fed-observation.json).
The [current profile](P4CORE_ACCEPTANCE.md)
requires 000001.SH, 399001.SZ, 399006.SZ and one eligible primary source.
KOSPI and SP500 are optional; legacy five-index snapshots are historical.

## Narrow source decisions

| Candidate | Verified from documentation | Decision for this profile |
| --- | --- | --- |
| AShareAPI | Its [daily K-line documentation](https://ashareapi.com/docs/endpoints/kline/) describes anonymous free daily requests. The [service page](https://ashareapi.com/) describes automatic upstream fallback. | Strongest new keyless technical lead, not selected. Exact three-symbol coverage, latest completed-session finality, per-response upstream provenance and upstream authorization for the intended uses are not verified. Do not call the endpoint on this record. |
| DataBull | Its [index documentation](https://www.databull.cn/docs/indices) explicitly names all three indices and its [authentication documentation](https://www.databull.cn/docs) requires a key. | Excluded under the current no-registration/no-key rule; the free tier does not remove authentication or settle rights. |
| CNEquity | The [project's source and license terms](https://rootsunc.github.io/CNEquity/legal-and-data-sources/) distinguish licensed client code from upstream data rights. | Useful discovery material, not an independent grant to obtain, keep or redistribute exchange data. |
| Tencent and BaoStock | Retained [Tencent](evidence/p4t-market-observation.json) and [BaoStock](evidence/p4b-market-observation.json) observations belong to the earlier limited probes. | No reusable permission, no fresh verified coverage; do not rerun a spent probe. |
| Official mainland market information | [SSE website notice](https://www.sse.com.cn/home/legal/) and [CNI data and licensing](https://www.cnindex.com.cn/zh_services/data_licensing/index.html?act_menu=4&act_service=3) distinguish access to published material from licensed uses. | Source for scope clarification, not blanket automated retrieval, retention, or transfer permission. |
| Federal Reserve Board press metadata | The [official RSS directory](https://www.federalreserve.gov/feeds/feeds.htm) and [copyright notice](https://www.federalreserve.gov/disclaimer.htm) support a narrow Board-authored primary-source candidate. | Use the existing `fed_press` configuration as the starting point of a new scoped proposal: attributed titles, links and release times only, exclude third-party material, no linked-page requests. The earlier observation is historical and its allowance is closed. |
| NBS RSS | The [official RSS directory](https://www.stats.gov.cn/wzgl/rss/) identifies the release feed; the [prior observation](evidence/p4n-nbs-observation.json) failed. | Not live-verified. Do not count the directory, failed request, or a renewed date on the previous observation as current evidence. |

## Exit criteria before live integration

1. For mainland data, identify the exact symbols, documented route, named upstream
   provenance and completed-session delay. Obtain a terms reference or written
   clarification that covers bounded anonymous retrieval, local retention and
   derived personal analysis. Decide model input and private delivery separately;
   neither is presumed granted, and public GitHub must receive no provider payload.
   For CNI and SSE, use the contact channels listed on their
   [service pages](https://www.cnindex.com.cn/zh_services/data_licensing/index.html?act_menu=4&act_service=3)
   and [SSE market service guide](https://www.sse.com.cn/transparency/services/index.shtml).
2. The grouped implementation contract must name the reviewed model, local or
   external execution boundary, budget, inputs allowed to leave the machine, and
   a private Telegram destination alias for the later delivery checkpoint.
   User credentials and destination identifiers do not belong in Git or chat.
3. Audit the exact contract first. Reuse existing adapters and the current
   three-index profile. A fresh, bounded Board RSS observation is separable
   from mainland acquisition, but first needs its own explicit operation scope
   and independent release within the grouped package. Record the actual times,
   deduplication, errors, and sanitized counts. Do not turn an older or synthetic
   observation into a live run.
4. Once enabled by the separately reviewed scope, complete three dated genuine
   end-to-end runs, a human evidence-support sample (20 claims or all if fewer),
   a three-part local report, a private test and linked correction. Finish through
   independent review, release, exact-head CI and a human merge.

Checkpoint status: mainland coverage 0/3, accepted dated runs 0/3.
The source/model/delivery/scheduling/trading/VPS flags remain closed. The
only new operation under review is the bounded Fed observation named in
the [P4FED contract](../contracts/p4fed_primary_news_verification.toml).
Even a fresh news observation cannot satisfy mainland coverage, live research
or private delivery. Do not amend the current acceptance denominator.
