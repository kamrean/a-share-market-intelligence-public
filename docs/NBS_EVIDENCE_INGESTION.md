# P4N Anonymous NBS Evidence Ingestion

## Scope and Actual User Decisions

PR10 merged P4I at 66be9c9705c5a9739f2beb5a3a9287f21703018d; exact-head PR CI44127995204 and
merged-main CI44269968648 passed on both platforms. All524 existing product tests
and127 governance protections remain. P4I is a pure composition API, not a live
collector or accepted research pipeline.

The user now requires free sources WITHOUT registration or API keys and explicitly
authorized ONE read-only GET to https://www.stats.gov.cn/sj/zxfb/rss.xml AFTER
independent Reviewer and separate Release Checker approval. Only NBS's own
title/link/publication metadata and acquisition evidence may be retained; no
article/attachment crawl, raw feed persistence, models, Telegram, trading or VPS.
EODHD/FSC key-based candidates are excluded from current integration, not declared
non-free or unlawful. BaoStock and all five exact benchmark gaps stay unresolved.
No paid provider, key/account inspection or registration. Model integration last.

This is one medium package: exact source transport, metadata acceptance/storage,
provenance-bound composition, offline tests, one authorized observation and review.
Do not split source/probe/bridge into independent construction PRs.

## Official Evidence and Limitations

- RSS guidance: https://www.stats.gov.cn/wzgl/rss/202302/t20230217_1912859.html
- Terms: https://www.stats.gov.cn/wzgl/202302/t20230217_1912857.html
- Exact feed: https://www.stats.gov.cn/sj/zxfb/rss.xml

The guidance explicitly supplies the latest-release RSS address and describes
automated subscriptions. Terms allow specified attributed use of NBS material and
statistical data, with third-party/specially restricted exceptions. This bounded
engineering assessment is not a general legal opinion. The RSS documentation does
not promise every item is own-authored or define publication timezone/schema.
Therefore metadata must prove the declared acceptance conditions or be rejected.
An exact NBS URL is necessary but NOT sufficient proof of own authorship.

Accept only item-level explicit NBS publisher/author/source attribution with no
conflicting author or special-rights ambiguity. Use a small exact label allowlist,
not an LLM or fuzzy guess. Missing/unknown attribution is excluded with counts,
not stored as a title. Unknown restriction metadata is excluded. Keep only
canonical HTTPS item URLs on www.stats.gov.cn beneath /sj/zxfb/; do not follow
them. Parse only explicit timezone-bearing publication dates. Date-only/naive,
future or malformed times are rejected; capture time is not publication time.
Do not manufacture upcoming events or infer a successful empty event day.

A feed with no admissible items, an inaccessible endpoint, a redirect, missing
timezone or unknown authorship yields a truthful BLOCK/degraded observation.
That is NOT permission to refetch, switch paths, inspect articles or relax rules.
Source availability and data acceptance are separate from engineering acceptance.

## Implementation and File Ownership

Product Builder owns only:
- market_intelligence/nbs.py (new)
- market_intelligence/source_policy.py (additive normalized_only support)
- tests/product/test_nbs.py (new)

Governance Builder owns only tools/governance.py, tests/governance/test_governance.py
and .github/workflows/governance.yml. Coordinator owns planning/docs/state/log and
verbatim actual verdict/validation records. No overlapping writes.

Use Python3.12 stdlib and existing bounded primitives: Store reservations/leases,
Capture/Evidence, safe paths/schema checks, Fact, P4I build/verify_replay_bundle.
No second market/research engine, credential loader, provider SDK or schema
migration. Existing modules/configs/demos and524 product cases stay byte-identical
except additive source_policy.py. Existing synthetic consumers still reject real
bundles; their behavior is not generalized in this package.

## Request and Storage Boundary

One fixed external store:
Path.home() / ".a-share-market-intelligence" / "p4n-nbs-ingestion.sqlite".
No configurable URL, source, transport, store path or authorization in production
CLI. Test seams use monkeypatch/temporary home, not public bypass switches.
Do not read the old Fed store or mutate the archived/private repository.

The production collect --allow-network path requires nbs-gate before DNS or store
creation. nbs-gate verifies current contract/plan/policy/branch/audits, protected
snapshot, full staged/worktree agreement, validation, independent Reviewer and
separate pre-probe Release. No --ci authorization. Persist fixed task intent and
request reservation before GET. Any previous attempt, including reserved/failed/
unknown, consumes the single allowance. Repeating returns recorded diagnostics,
never another request. Preserve interruptions and unknown accounting.

Use verified TLS, exact hostname/path, public DNS binding and a total10-second
transport deadline; max512000 bytes,100 RSS items, bounded XML depth/nodes and
no DTD/entity expansion. No redirects, proxies/cookies/netrc, compression, retry,
host fallback or article/attachment retrieval. Unknown byte usage stays unknown.

Transport bytes exist transiently only. NEVER write raw XML or descriptions to
disk, logs, public evidence or normalized fields. Save admitted title/link and
explicit publication timestamp with capture/response hash, item identity, parser,
run/attempt/request identity and actual retrieval/first-seen timestamps.
Rejected items retain only reason counts and non-content digests where necessary.
Current normalized content expires within86400 seconds of first possession.
This limit is project policy, not an invented source legal deadline.

Physical Capture retention is metadata_only; the body is absent. Add a distinct
normalized_only source-policy class so normalized metadata can have its own
bounded positive expiry while raw_retention MUST be denied. Preserve original
metadata_only/restricted policy behavior. Normalized titles remain real retained
content, not exempt from expiry just because they are metadata.

## Connection to P4I and Read Paths

Connect accepted stored Evidence to Fact objects, preserving source identity,
original times and original as_of. Reconcile run/attempt/event/capture/record
counts, normalized hashes and original rejection counts before composition.
No caller-authored list can silently replace the actual acquisition records.
Use current-clock guards before payload use and after expensive work/before
return; reject clock rollback, expired/tombstoned content and inconsistent state.

The private in-memory result contains a provenance wrapper and an unmodified
P4I inner replay bundle. Do not rewrite its schema/readiness/operational flags
to pretend a pure API is acquisition authentication. Bind the wrapper to run,
attempt, original input metadata, source-policy and composition fingerprints.
Report metadata_auditable_only and raw_replay_unavailable. Without retained raw
bytes, parser replay and cryptographic authentication of the remote feed cannot
be promised. Hashes and asserted source attribution do not prove external truth.

status is sanitized and read-only. compose checks the fixed existing store and
current retention, then produces private in-memory composition with only summary
counts/IDs on stdout. Missing stores are not created; wrong-schema/WAL/linked/
foreign/tampered stores are rejected. Reuse safe metadata/read-only infrastructure
where compatible without altering historical Fed behavior. sweep explicitly
tombstones expired normalized payload and preserves attempts/history, never resets
the request allowance. Do not retain payload in source summaries or frozen bundles
that survive cleanup; cleanup must remove EVERY persisted title-bearing copy.

Methods have the fixed five benchmarks, no fabricated constituent/sector mapping,
no invented calendar, no flow data and no future-event declarations. Keep
market/sector/event/entity gaps honest and required domains blocked. News cannot
promote index coverage. No model research runs or accepted reports are produced.
Only genuine source acquisition, if successful, is a new live source observation;
subsequent composition is historical_replay, never a reconstructed live run.

## Audit, Observation and Publication Sequence

1. Freeze planning inputs. Actual independent entrypoint and Prompt Auditor PASS.
2. Disjoint Builders implement. Offline tests block all real network and use only
   authored fixtures/temporary stores. Preserve all historical tests.
3. Freeze protected code/state/log, run complete validation and independent
   Reviewer. Default one in-scope R0/R1 repair plus one re-review total.
4. Separate independent Release Checker authorizes the exact one-shot probe only
   after checking Reviewer and validation. Record LOCAL_READY_FOR_NBS_PROBE.
5. Execute the authorized GET once, preserve terminal result including failure,
   unknown or unusable metadata. Generate sanitized observation via code, not
   hand-written successful counts. No retry or alternative observation.
6. Final separate Release Checker inspects actual observation and frozen snapshot.
   Record LOCAL_READY_FOR_PR; commit/push/PR, exact-head dual-platform CI and remote
   release; stop AWAITING_HUMAN_MERGE. Never direct/auto merge/admin bypass.

Exactly six current hash-exempt attestations: bundle, validation, Reviewer,
pre-probe Release, final Release and NBS observation. All older artifacts remain
immutable. Pending pre-publication text in frozen state/log is historical; later
observation/CI/merge facts live in their narrowly typed attestations/PR, not an
unreviewed rewrite of permissions. A terminal source BLOCK is permitted evidence,
not automatically a code-review BLOCK; software failure still requires proper
repair/escalation and cannot be hidden as a source limitation.

## Progress and Next Gate

Prior completed: P0-P4I offline foundation, guards, publication and composition.
This package: anonymous NBS metadata acquisition and provenance connection only.
Remaining: all five benchmarks, A-share sector/calendar/flow/event inputs, real
model/research/report/private Telegram integration, three genuine research runs,
claim sampling/MVP acceptance, then deferred forward observation and VPS.
Every work-package final report must explicitly separate these three categories.

## Explicit Bounded Repair Exception

After the default R1 clock-diagnostic correction, independent Reviewer
01a1eec4-2066-76f1-9ba5-7dad5674a563 blocked snapshot
2ff1ca0f9a5e2dc97e81372a2c556707810fc03e7528ba90bb2a1566cee4d06d:
unknown namespaced or nested channel-level rights could be ignored and restricted
titles retained. The user explicitly authorized ONE additional bounded R1.

The repair_exception is identical in contract, policy and state and pins the
prior BLOCK digest, finding, same Reviewer and two product paths. Default
max_repair_cycles remains1; only this P4N exception permits total2. It cannot
carry into another package, reset prior repairs, change the source or authorize
another request. Preserve original audits/BLOCK in the protected construction log.
Re-audit these planning changes before product/checker repair. Update only the
necessary checker bindings/tests for this exact exception; never globally raise
historical review limits. Final review must report repair_cycles2 and the same
Reviewer UUID. Another BLOCK requires new human escalation, not another loop.

Repair only channel-level restriction recognition in nbs.py and regression tests
in test_nbs.py; no source_policy.py changes or other product edits. Reject unknown
namespaces/nested rights before retaining any affected title. Preserve ordinary
valid feeds, item-level rules and original parse/rejection denominators. Full
regression, same independent Reviewer, separate Release, one authorized source
observation and exact-head CI remain required. No operational permission changes.
