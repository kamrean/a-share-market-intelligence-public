# P1 Design and Acceptance Boundaries

## Deliverable and Source Coverage

P1 is one Stage1-2 foundation package, not a complete research product.
Five benchmark targets remain fixed: Shanghai Composite 000001, Shenzhen
Component 399001, ChiNext 399006, KOSPI, S&P 500. Free access, permitted automation,
credentials, storage rights, provider implementation and successful live access
are separate facts. Unknown rights block capture, not merely redistribution.

Only the Board of Governors press RSS is selected for an initial bounded live
probe. Its official RSS guidance and disclaimer support low-volume aggregation
of Board-authored information with attribution, excluding protected third-party
material and insignia. No linked pages or attachments are fetched automatically.
Market and paid adapters expose explicit unavailable/disabled states; offline
parser tests never imply that the benchmarks are live-connected.

Sources, actual observations and access limitations are in SOURCE_MATRIX.md,
config/sources.toml and evidence/p1-source-probe.json. No unexplained 100% claims:
retain request, parse, accepted-record and benchmark-coverage denominators.

## Runtime Modules

Use Python 3.12 standard library, SQLite, dataclasses/enums, explicit serialization
and validation. No third-party runtime dependency, services or framework.
requirements.lock documents the empty dependency set.

- models.py: typed boundary records, strict UTC-aware time parsing, run modes,
  source/retention/evidence metadata and finite numeric validation.
- sources.py: catalog validation, allowlisted bounded HTTPS transport, safe RSS
  parsing, strict market CSV parser and always-disabled paid adapter boundary.
- store.py: SQLite transactions, append-only source versions, run/task identity,
  leases, durable request reservations, bounded storage, tombstones and bundles.
- gate.py: profile-driven domain validation, universe/temporal checks, explicit
  session calendar interface, conflicts and confidence ceilings.
- runtime.py and __main__.py: offline synthetic demo, explicit opt-in live probe,
  retention sweep and verified replay. No automatic scheduler.

The public API between modules is deliberately small; implementation owns its
concrete record types. Serialization must validate on read as well as write.
Never evaluate content as instructions or construct executable actions.

## Temporal and Evidence Rules

Capture time is actual local UTC observation, not an HTTP header or publication
timestamp. Preserve known source offsets. Unknown occurred_at/effective_at remain
null; RFC publication time does not prove event occurrence. First-seen records
cannot be moved backward by replay or later retrieval.

Every live input version must have retrieved_at and first_seen_at at or before
as_of. Future/naive/contradictory timestamps and mixed synthetic/replay inputs
cannot pass a live profile. Source quality records sample counts; a one-request
success is not reliability proof.

A news domain may pass while the market domain blocks. Required domains without
validators or sufficient declared coverage block globally. Optional gaps can
degrade only under a predeclared rule; rejected evidence is excluded explicitly.
Do not fill missing observations with zero or prior values. Tier C alone cannot
confirm facts; conflicts stay visible, including across eligible source versions.

Only explicit versioned session records are calendar input. No production
holiday calendar is inferred from weekdays. China uses Asia/Shanghai, Korea
Asia/Seoul and the US America/New_York as market identities; a session supplies
its actual timezone-aware closing instant, including exceptional/early closes.

## Storage, Retention and Safety

Idempotent tasks do not dispatch again after successful completion. Concurrent
work uses transactional leases; stale owners cannot renew/release a newer lease.
Expired leases record interrupted work. Interrupted/unknown requests retain
conservative reservations; budget cannot reset by retry or process restart.

Reserve before each attempt: at most 2 source requests/run, 12/UTC-day, 512000
response bytes/attempt, 10-second timeout, one retry and concurrency one.
Persist source-request accounting; model/delivery budgets are not implemented
or authorized in P1. Cap SQLite storage at 10 MiB conservatively.

Transport uses exact approved HTTPS endpoint, normal TLS verification, no
ambient credential/proxy inheritance, no redirects or private-address targets,
bounded responses and safe content types. Do not follow feed links. Reject XML
DTD/entities/unsupported encodings rather than resolving them. Tests inject
transport and cannot touch live HTTP or DNS.

Retention permission is checked before any write. restricted means no payload;
metadata_only cannot replay. short_term_raw expires at the specified time and
an explicit sweep deletes raw payload, leaving a tombstone and permitted
provenance. Replay requires retained bytes matching the recorded hash/parser
version and records historical_replay, never historical live observation.
Read paths enforce expiry even when the sweep has not yet run.

Frozen bundles store immutable references, source/content fingerprints and
profile/calendar/method versions, not duplicate raw data that defeats expiry.
Mutation creates a new version. The store must not silently overwrite a bundle.

## Acceptance and Boundaries

Offline acceptance covers parsing, clocks, missingness, conflicts, domain
aggregation, immutable bundles, replay expiry/hash mismatch, idempotency, leases,
request budgets, unknown usage and network/secret/path rejection.

A live probe records actual request/time/result. On permission/key gaps, do not
send a request just to produce a failure. Record not_attempted and the reason.
Synthetic demo can PASS its explicitly synthetic profile; live market research
readiness remains BLOCKED_MARKET_SOURCES until every target has valid evidence.
Neither result implements claims, research agents, Telegram, sector methods or
VPS. Those require later contracts.

Use one PR after validation and independent reviews. Keep existing protection;
record actual CI and human merge on the PR. P0 historical receipts remain intact.
