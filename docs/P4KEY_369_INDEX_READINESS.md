# 369 daily archive candidate

The operator confirmed coverage for the three required mainland benchmarks.
The provider's [free tier](https://www.369.top/pricing) advertises 500
requests per day, and its [daily archive specification](https://www.369.top/docs/a/day_kline)
documents post-close availability around 16:00 Asia/Shanghai. This is a
candidate collection cutoff, not evidence that these bars are final or that
late revisions are impossible.

| Required benchmark | Requested provider ID |
| --- | --- |
| `000001.SH` Shanghai Composite | `sh000001` |
| `399001.SZ` Shenzhen Component | `sz399001` |
| `399006.SZ` ChiNext | `sz399006` |

Use the exact first-party HTTPS endpoint, two already known contiguous
exchange sessions, unadjusted close fields only and a fixed small response
limit. Check the echoed provider code, adjustment marker, exchange dates and
close values; reject unknown rows and conflict rather than silently picking
a duplicate. Freshness only compares to the expected dates. No result is a
published index value or evidence of actual availability.

The provider's [SDK 0.9.4 release](https://pypi.org/project/top369/0.9.4/#files)
checks for business-envelope code 200. This behavior was confirmed by
reading the published, checksum-matched wheel without installing or running
the SDK.

The provider's [terms, sections IV and V](https://www.369.top/terms) describe
internal research, no redistribution, and rights held by underlying sources.
The source path and eligible fallbacks, precise index finality/corrections,
and permitted retention and downstream use are not yet established.
Registration, a key, the operator's symbol confirmation and authored
fixtures do not establish those facts.

No API key is loaded in this package. There is no live client, CLI, scheduler
or source-registry entry. Real requests: 0. The existing core preflight
stays offline and its historical anonymous profile is unchanged. CI uses
authored shapes only. Do not put responses, points, or credentials in Git.
The next real test requires source-located terms or a written confirmation,
concrete bounded dates and limits, independent pre-dispatch approval and
explicit authorization. Model, Telegram, trading and VPS remain closed.
