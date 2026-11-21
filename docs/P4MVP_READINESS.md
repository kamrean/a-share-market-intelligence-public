# Offline MVP Readiness

P4MVP is a candidate built on human-merged PR14, not formal research MVP
acceptance. It adds a single repeatable demonstration on Python 3.12:

```text
python -B -m market_intelligence.mvp demo
python -B -m market_intelligence.mvp demo --json
```

No installation beyond Python 3.12 is needed for the demo. Existing optional
BaoStock SDK tests still use the separately hash-locked binary dependencies.
The demo uses authored fixtures and a disposable workspace. It runs the existing
evidence gates, bounded authored committee, local three-part report, ledger,
linked correction and local simulated delivery. It sends nothing to Telegram.
Rendered prices and claims are synthetic and not investment recommendations.

## Evidence and Scope

Historical [sanitized diagnostic counts](evidence/p4mvp-tencent-shape.json)
establish only that on a separate request, ten row rejections were categorized
as non-scalar values after the first six fields. The earlier response cannot be
reconstructed. The offline candidate preserves the original strict entrypoint,
symbol, date/session, OHLC, window and field limits. It tolerates a bounded JSON
object or array in an optional trailing position without evaluating it or
treating it as a market value. Full original rows still determine duplicates
and conflicts. There is no new network entrypoint. Source rights remain unknown.

| Blueprint v1 requirement | Status in this candidate |
| --- | --- |
| Credential-free repeatable demo and ordered reports | Offline engineering evidence; fixture facts only |
| Exact five-index coverage plus a permitted primary source | Historical official-news probes do not close the 0/5 benchmark gate; use rights and usable observations unresolved |
| Three dated end-to-end live runs | Not run |
| Deduplication, sourced events, mapping, rotation, honest unavailable flow | Existing offline methods/tests; not proof of live discovery or flow availability |
| Genuine research/review/editing and cost records | Authored demo only; no approved provider or budget |
| Sample actual evidence support for 20 claims or all if fewer | Deferred until actual claims; no quality percentage |
| Local report, linked correction and authorized private Telegram test | Local demo and fake delivery only; no private send |
| Offline negative tests, artifacts, provenance and human merge | Candidate review and exact-head CI; the PR remains a human decision |

Fastest path after this PR: settle permitted sources and benchmark scope first.
Then group provider configuration, the three real end-to-end observations,
claim audit and one private delivery/correction in a separately approved
acceptance package. Leave forward observation and VPS deferred. No new use
rights, collection quotas or deployment are granted by this candidate.

Repository automation performs scoped snapshot and receipt validation, existing
Ubuntu/Windows CI and the mandatory human merge gate. Codex subagents handle
bounded implementation and independent read-only review and release.
