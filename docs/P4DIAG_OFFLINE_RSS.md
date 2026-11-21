# Offline RSS diagnostics and mainland source evidence

This is an offline follow-up to human-merged PR #17
(`7a036af0e540ba1526b20330b18779e2cc71d421`). It is not permission to connect to a source.
The predecessor [merged-main checks](https://github.com/kamrean/a-share-market-intelligence-public/actions/runs/44922015663)
are the construction prerequisite. The
[original result](evidence/p4fed-observation.json) stays FAILED / invalid_response
with one consumed attempt. The original response was not retained and its
specific rejection cause cannot be recovered. Authored cases can reproduce
possible failure classes; they cannot identify which one actually happened.

## Diagnose without fetching or retaining an item

```text
python -B -m market_intelligence.rss_diagnostics demo
```

The demo uses authored in-memory examples and prints closed category codes and,
on a parsed response, aggregate freshness, duplicate, conflict and rejection
counts. No feed body, real item field, URL, exception text, or local file path is
included in diagnostic output. It has no endpoint or input-file options. This
does not change the original transport or parser. The underlying response limits,
Board-only attribution, publication timestamps and conflict policy remain in force.

Compare the [RSS Advisory Board specification](https://www.rssboard.org/rss-specification)
with the [official Board RSS directory](https://www.federalreserve.gov/feeds/feeds.htm)
and [Board reuse terms](https://www.federalreserve.gov/disclaimer.htm).
Conforming RSS format is separate from a project's publisher, locator, freshness
and retention requirements. A safe reason code is not a source grant, an
operational result, or a reason to loosen admission rules.

## Mainland candidates revisited

| Evidence | Interpretation | Status |
| --- | --- | --- |
| [AShareAPI daily bars](https://ashareapi.com/docs/endpoints/kline/) and [terms, section 6](https://ashareapi.com/terms/) | Anonymous daily bars are documented and provider terms allow own-application or research analysis within their limits. The index code documentation identifies sh000001, sz399001 and sz399006, not successful finalized daily observations for all three. | Best current technical candidate; no live approval. |
| [AShareAPI source field](https://ashareapi.com/wiki/envelope/) | The provider says the field is not stable evidence of a specific upstream. A published polling interval is not the current session's finality and correction rule. | Covered upstreams, comparable finalized bars and raw retention are unresolved. |
| [DataBull index docs](https://www.databull.cn/docs/indices) and [authentication](https://www.databull.cn/docs) | Documents the three symbols, but requires a key. | Excluded by the current no-registration/no-key profile. |
| [AIGU developer docs](https://www.aiguq.com/docs.html) | The free index snapshot is a connectivity tool. It does not document a keyless completed-session series and retention grant. | Not selected. |
| [SSEINFO licensing](https://bsp.sseinfo.com/license/) and [CNI data services](https://www.cnindex.com.cn/zh_services/data_services/index.html?act_menu=null&act_service=2) | Exchange-side permission and contracted data services are separate from viewing published pages. | Use for confirmation, not as a product feeder. |

Before writing a live operation contract, obtain current written or public
evidence naming the provider and all permitted fallback upstreams, all three
symbols, access cadence and fields, the Shanghai-time completion and corrections
rule, bounded retention, and local derived-analysis scope. Confirm external model
input, private messaging, and public release separately; no raw provider material
belongs in Git. Provider contacts are listed in the linked terms and official
service pages. They were not contacted in this audit.

Mainland verification remains 0/3, genuine dated runs 0/3. Unavailable KOSPI and
SP500 do not block the core, but cannot replace it. No live news, mainland
collection, model, Telegram, scheduling, trading, or VPS is enabled.
