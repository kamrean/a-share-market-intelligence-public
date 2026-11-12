# Market Intelligence System Blueprint v1

## 1. Purpose and Delivery Target

Build a personal, read-only market intelligence and multi-agent research system. It focuses on A-share markets and supplements them with Korean and U.S. market context. It collects source-located market, news, event, flow, and sector information, produces evidence-backed research, and delivers informational reports through Telegram.

The first release is a research MVP: a small, reproducible end-to-end pipeline for collecting evidence, analyzing market context and producing traceable daily reports. Delivery is evaluated through working functionality, transparent methods and genuine execution evidence. This document remains Blueprint v1; amendments use Git history, not a new blueprint version. MVP acceptance, observation acceptance, and VPS release are separate milestones. A GitHub upload alone proves none of them.

Free, permitted information sources are the MVP default. Paid providers get a disabled adapter boundary and contract tests; without credentials and successful authorized requests, they are not described as connected or live-verified. Model/API costs are separate from source costs.

## 2. Non-Goals and Permanent Boundaries

- No broker integration, order placement, position sizing, portfolio control, or automated execution.
- No investment return guarantee, executable trade instruction, or hidden action semantics.
- No public, paid, or third-party advisory service without a separate compliance program and explicit authorization.
- Research may state attention, confirmation, risk, timing windows, counterarguments, and invalidation conditions.
- Factual index levels, historical prices, traded volume, turnover, and methodological statistics are allowed. Order prices, order quantities, target positions, allocations, and machine-action instructions are forbidden.
- The new project's Stage 8 is research evaluation. It is unrelated to the archived ETF project's trading-readiness Stage8 and inherits none of its gates or permissions.

## 3. Core Chain and Implementation Shape

Raw Evidence -> Normalized Evidence -> Domain Evidence Gate -> Frozen Evidence Bundle -> Claims -> Append-Only Research Ledger -> Publication Guard -> Delivery -> Outcome Evaluation

Preserve the distinction between fact, unconfirmed lead, inference, scenario, and observed market reaction. Use one modular Python application, SQLite, typed/validated boundary records, and a simple scheduler/CLI. Establish the supported Python version and reproducible dependency lock before adding third-party runtime dependencies. Do not begin with microservices, a distributed queue, a vector database, or a general agent platform.

Construction agents and product research agents are separate. The deployed program uses configured model adapters, not a permanently open Codex chat. A graph framework is optional only if a later contract demonstrates a need; fixed research steps do not require one.

## 4. Operating Principles

- Evidence and deterministic validation precede language-model interpretation.
- Every published factual or analytical claim has an identifier and evidence references.
- Citation existence is not proof of entailment, source truth, or causation. Semantic review and sampled human assessment remain necessary.
- Chief Editor may only compose approved claims. New or materially changed claims return for review.
- Published research records are append-only; corrections create a new record with a supersedes relation.
- Publication Guard is deterministic. Agents cannot waive it or directly send Telegram messages.
- Source authority and operational quality are separate: Tier A is an accountable primary source for its own disclosure; Tier B is an identified secondary source with provenance; Tier C is an unverified lead. No tier guarantees truth. Conflicts remain visible.
- Tier C sources cannot independently confirm a fact. Preserve unconfirmed labeling until corroboration satisfies the report's declared evidence policy; citation or entailment checks alone cannot promote a rumor to fact.
- New sources start with operational quality unknown. Compute availability, correction, freshness, and parse reliability from observed samples; record sample size and avoid invented precision.
- Compute sector and flow statistics with documented methods. LLMs explain and challenge them; they do not manufacture missing values.

## 5. Cross-Stage Controls

### 5.1 Domain-Scoped Evidence Gate

Evaluate market, flow, news, event, entity mapping, and cross-market context independently. Each report profile pins required/optional domains, expected universe, freshness, minimum coverage, and DEGRADED behavior before running. A required domain without a validator is BLOCK. Global PASS cannot mask a required-domain BLOCK. Optional missing or stale data is explicitly unavailable/DEGRADED and lowers the relevant confidence ceiling; it is never zero-filled or silently replaced.

P1 must verify actual free-source access, time semantics, permitted usage, and coverage before freezing the source matrix. P2 freezes the report-specific market/flow/sector definitions. Failed probes remain recorded gaps, not completed integrations. Changing coverage targets requires a reviewed contract amendment.

### 5.2 Retention and Replay

Each source has a retention class: metadata_only, short_term_raw, reprocessable_raw, or restricted. Record source_id, locator, retrieval time, content hash, content type, source timestamp, parser version, retention basis, expiry, and replayability. Retain raw content only as permitted; free access does not imply redistribution rights.

A hash or link alone cannot reconstruct evidence. Label records replayable, metadata_auditable_only, or expired_unavailable. Parser replay guarantees apply only to retained, permitted inputs. Expired events leave active retrieval but do not erase permitted ledger provenance. Deletion removes restricted payloads; append-only correction/tombstone metadata explains the change. Public fixtures and samples must be independently distributable or synthetic and clearly labeled.

### 5.3 Temporal Semantics

Use timezone-aware occurred_at, published_at, retrieved_at, first_seen_at, effective_at, as_of, and observed_at where applicable; distinguish unknown from inferred timestamps. For live research, the referenced version must have been captured and first seen at or before as_of. An earlier publication timestamp alone does not establish that the system possessed the evidence.

Record run mode as live_observation, historical_replay, or synthetic_test. Later retrieval of old news cannot be represented as a historical live observation. Freeze source versions, calendar/session, entity/sector mapping version, and methodology with each bundle. Later evidence creates outcomes/corrections, never silent backfill. Each market uses its own trading calendar; weekend news retains its real release time.

### 5.4 Global Cost Guard

A deterministic runtime guard bounds calls, tokens, rounds, concurrency, source requests, retries, storage, and delivery volume per run/day. Reserve a conservative budget before dispatch, cap output tokens/timeouts, reconcile usage afterwards, and stop new dispatch when usage is unknown or limits are reached. A quota refusal may block/defer/degrade with a recorded reason; it cannot silently skip required review.

Construction budgets are separate from runtime budgets. Keep bounded review loops and compact review artifacts; do not claim exact provider billing when usage is unavailable. Free sources do not imply free model execution.

### 5.5 Protected Merge Policy

No agent direct merge, direct push to an established protected main, or administrative bypass. Every change requires validation and independent review; merge mode is determined by the highest-risk touched surface and explicit contract:

- Tier A: non-canonical documentation/formatting and isolated fixtures may use protected auto-merge only when explicitly enabled.
- Tier B: isolated low-risk tooling may use protected auto-merge only with no canonical, schema, runtime, or permission effect.
- Tier C: blueprint, contracts, policy, schemas, collectors, gates, ledger, model, and Telegram changes require human merge.
- Tier D: permissions, credentials handling, deployment, and production external side effects additionally require an independent release decision.

Defaults are human merge and auto-merge disabled. Mixed changes take the stricter tier. Unknown classification escalates. This policy does not require a separate PR for each internal task or review role.

### 5.6 Read-Only Semantics and Untrusted Input

Read-only permits authorized external retrieval and informational delivery. It does not permit mutating brokerage, exchange, portfolio, account, or order state. Market price/volume facts are not trade instructions. Do not implement broker credentials, trading endpoints, order schemas, trade-intent/final-action fields, or equivalent executable outputs.

Treat news, webpages, retrieved documents, and tool results as untrusted data, not instructions. Collectors enforce approved destinations, bounded responses, and safe parsing; research agents receive bounded records without secrets or execution privileges. Validate structured outputs and supported claims before deterministic rendering. Prompt injection, unsupported-claim, and forbidden-action examples belong in negative tests.

## 6. Governance and Conversation Model

Use one user-facing coordinator conversation. It owns requirements, integration, stage state, and handoff. It may delegate bounded tasks to independent subagents. One writer owns a file set; parallel writers require disjoint sets. Do not maintain separate permanent blueprint/builder/reviewer chat windows.

Prompt Auditor, Builder validation, independent read-only Reviewer, Release Checker, and CI remain distinct recorded responsibilities, not necessarily permanent agents. The Builder cannot supply its own independent review. Reviewer reads the contract, actual diff, tests, and evidence directly, in a separate context, and waits for all required results before PASS. Checks and CI cannot impersonate Reviewer PASS. Production Tier D release requires a separate independent release reviewer.

A work package contract specifies objective, dependencies, exact allowed/forbidden surfaces, deliverables, acceptance/negative tests, rollback, merge tier, repair bound, and next gate. R0 means documentation/test-evidence correction without behavior or scope expansion; R1 means a bounded implementation correction inside the existing contract. One in-scope repair cycle and one re-review are permitted by default. R2+ scope/permission changes, unknown findings, or a second BLOCK require human escalation. Never repeat reviews merely to obtain PASS.

Repository state, contracts, review records, and commit hashes are the durable handoff. New conversations read a small entrypoint plus active scope and follow named references. The old repository is not default reading material. At a package boundary or context-quality problem, record a checkpoint and continue in a fresh conversation; no guarantee depends on unlimited chat history.

Finalize decision-affecting state, policy, contract, and construction-log content before independent review. Bind approval to a content snapshot; changing its inputs invalidates approval and requires validation and review again within the repair bound. Only explicitly enumerated append-only verdict/validation/commit/CI/merge receipts may be attached afterwards as attestations, not as new permissions or stage decisions. Release acceptance must identify the approved snapshot and, when remote CI exists, the exact tested PR head. Never use a receipt to mark a gate passed without its actual evidence.

### 6.1 Reviewed Repository Bootstrap

Repository creation requires explicit owner/name/visibility authorization. A clean publication import uses a fresh repository, never a clone/fork/mirror of private history. The original private repository remains audit evidence, not a second active construction target. The exact source merge and historical CI are recorded separately from current publication acceptance.

Independently review the sanitized Blueprint v1 and construction entrypoint first, then commit only those two documents as the fresh root baseline. Pin its resulting full hash in the active contract, checker, tests and CI; do not amend it. After the publication contract, Prompt Auditor, validation, independent Reviewer and local Release Checker pass, initialize remote main with that reviewed baseline only. This one-time initialization does not permit construction changes directly on main.

Configure public main protection before pushing the reviewed construction branch: PR required, both named CI jobs required from GitHub Actions, administrators included, force pushes/deletion disabled, and auto-merge off. A sole maintainer uses zero GitHub approving-review requirements to avoid self-approval deadlock; independent agent review and human merge remain mandatory. This is not a claim that GitHub enforces the human identity of the merger.

Frozen pre-publication receipts use NOT_RUN_AT_SNAPSHOT and LOCAL_READY_FOR_REMOTE_BOOTSTRAP, never remote success. Subsequent exact-head CI, privacy inspection and protection verification belong on the PR. Public output includes only reviewed, sanitized files and evidence summaries; original private approvals cannot approve changed bytes. P1 waits for the public import PR's actual human merge and its own reviewed contract.

### 6.2 Default Capabilities and Stage Permissions

Project capability ceilings: research_only=true; broker_allowed=false; order_execution_allowed=false; portfolio_control_allowed=false. A capability ceiling is not current stage authorization.

Stage 0 effective permissions: external_data_access_allowed=false, scheduled_collection_allowed=false, llm_research_allowed=false, telegram_delivery_allowed=false, vps_deployment_allowed=false, vps_mutation_allowed=false. These restrict the product and operational actions, not authorized construction subagents or read-only development documentation research.

Later contracts may enable collection, research, and private test delivery individually. Production Telegram and VPS require separate release authorization. No stage can authorize trading through an inherited flag.

## 7. Stage Plan and Medium Work Packages

Keep Stage 0-9 as capability identifiers. Deliver them through the following medium packages, with internal tasks rather than a PR per task:

| Package | Stages | Deliverable |
| --- | --- | --- |
| P0 | 0 | Project contract, lean governance, reuse inventory, state, offline checker tests, CI definition and handoff |
| P1 | 1-2 | Tested free-source matrix, capture/normalization, task records, evidence gate, retention and replay |
| P2 | 3-4 | Market/sector methods, bounded news/event pipeline, calendars and domain validators |
| P3 | 5-6 | Fixed evaluation criteria, bounded research/rebuttal/editor workflow and cost guard |
| P4 | 7 plus MVP subset of 8 | Daily three-part report, ledger, corrections, private test delivery, replay/evaluation and MVP acceptance |
| P5 | remaining 8 and 9 | Longer observation/calibration and separately authorized VPS release; deferred beyond MVP |

Each package normally produces one coherent PR after review; split only for an actual dependency, risk, or reviewability reason. P4 acceptance does not mark all of Stage 8 or Stage 9 complete.

### Stage 0: Project and Governance

One package includes a source-located legacy reuse inventory, project contract, small policy/state registry, checkers, checker tests, deterministic prompt/review bundle generation, CI definition, and construction log. Reuse patterns selectively; do not transplant the old ETF governance tree or business code. No runtime collectors, model research, deployment, or Telegram work.

Exit: locally verified governance can be reviewed reproducibly; merged completion additionally needs remote CI and human merge. Next-package implementation waits for that merge and its contract.

### Stage 1: Sources and Runtime Foundation

Probe permitted free sources and publish a coverage/limitations matrix before selecting them. Capture bounded raw/normalized evidence, task runs, locks, idempotency, retries, and logs. Record retention before capture. Provider states distinguish configured, implemented_unverified, live_verified, degraded, disabled, and unavailable. Paid adapters default disabled and must not make requests in tests without explicit opt-in.

### Stage 2: Evidence Gate Core

Implement schemas, freshness, missingness, conflicts, source authority/quality, timestamps, domain validators, confidence ceilings, freezing, and calendar interfaces. Register entity/event validators later without depending on absent systems. Only PASS or explicitly permitted DEGRADED bundles reach research.

### Stage 3: Market, Flow, and Sector State

Initial market target: at least three named mainland benchmarks, one Korean benchmark, and one U.S. benchmark, with exact symbols, source, delay, and calendar pinned by P1. Latest completed sessions are sufficient; real-time or full-market coverage is not promised.

Implement documented breadth/turnover and sector-rotation measures over an explicit bounded universe. Preserve provider, methodology, universe, timestamp, coverage, and lookback. A missing historical window is insufficient_history, not a fabricated percentile. Provider-estimated flow, exchange statistics, and turnover proxies are different series and must never be mislabeled as each other. A flow adapter and missing-data path are required; unavailable live net-flow data remains a disclosed feature gap, not a claim of implemented full flow coverage.

### Stage 4: News and Event Intelligence

Use a small approved source set; deduplicate, rank with explainable factors, decay stale items, map entities, and preserve evidence. Event states cover discovered, scheduled, approaching, awaiting_confirmation, confirmed, delayed, cancelled, expired, and superseded. Separate event success, expected sector impact, and observed market reaction. Upcoming events must be sourced, not generated from model memory.

### Stage 5: Evaluation Framework

Before first research publication, freeze citation/entailment sampling, freshness, mapping, duplicate-rate, counterargument, alert-usefulness, and coverage measures with denominators. T+1/T+3/T+5 use the mapped market's trading calendar and a declared anchor price/time, benchmark, corporate-action policy, and missing/suspension handling. Separate forecast stance from subsequent observation; returns are descriptive, not proof of causality or tradable profit.

### Stage 6: Research Committee

Use bounded roles: researcher, independent counterargument/risk reviewer, and editor. Separate role contexts; at most two research rounds. Pin model/prompt versions, input/output limits, retries, timeout, budget, and failure handling. Same-model agreement is not independent factual verification. Model adapters are configurable and tests run offline; genuine model integration needs an authorized live run. Unavailable credentials or review failure cannot be disguised by a mock report.

### Stage 7: Ledger and Publication

Persist research_id, bundle_id, claim_ids, stance, counterarguments, invalidation conditions, as_of, model/prompt/method versions before delivery. Deterministic publication checks verify contract, provenance, freshness, required domains, prohibited actions, limits, and correction links.

Use durable delivery records with report/part identity. Distinguish queued, sent, failed, and unknown outcome after a timeout. Do not promise exactly-once remote delivery or blindly retry an ambiguous send. Test part ordering, chunking, rate limits, partial failure, and corrections. Telegram is an adapter; Hermes is optional, not a required platform dependency.

### Stage 8: Outcome Evaluation and Calibration

MVP includes outcome schema, deterministic evaluation, labeled historical replay, and a quality/error report. Real forward observation is recorded separately with genuine run IDs, time windows, interruptions, and matured outcomes. Later knowledge cannot rewrite prior claims. Long-window calibration is deferred; synthetic/replayed data never count as forward observation or accuracy proof.

### Stage 9: Data-Only VPS Release

Deferred beyond research MVP. A: environment and rollback readiness with deployment disabled. B: explicitly authorized shadow VPS, production Telegram disabled. Observe 10-15 actual trading days with truthful runtime, interruptions, source health, and report quality. C: independent release review and explicit production informational-delivery authorization. This proves operational readiness within its sample, not predictive profitability. Broker, orders, and portfolio control remain forbidden.

## 8. MVP Reports and Acceptance

MVP implements daily_brief and correction. One logical daily_brief has three ordered parts: (1) sourced news and upcoming events, (2) indices and available flow/turnover context, (3) sectors, impact hypotheses, counterarguments, and invalidation conditions. Render locally without credentials; private test Telegram delivery requires P4 permission and a user-designated destination.

Overnight, standalone premarket/intraday/midday/close, and weekly automated products are deferred. Each part links report identity, coverage/as_of, sources, limitations, and correction identity. Empty material-news days say so; never invent content to fill a quota.

Research MVP acceptance requires all of the following:

1. Clean-environment installation with a pinned dependency lock and documented commands. Offline synthetic/permitted replay runs without API keys; runs and output are labeled accurately.
2. The P1 market target and at least one permitted primary news/disclosure or official event source are live-verified. Retain evidence of at least three dated end-to-end live research runs; report failures, missing domains, and exact observation dates. This is an integration sample, not statistical validation.
3. Demonstrate deduplication, sourced upcoming events, sector mapping, transparent rotation/turnover methods, and the real-or-unavailable flow path. A recorded empty event day is valid; a test fixture does not prove live event discovery.
4. Demonstrate actual model-backed research/review/editing, bounded rounds and cost accounting, and evidence-linked output. A stubbed model only satisfies offline tests, never live integration.
5. All published substantive claims have valid evidence links. Audit at least 20 sampled claims, or all if fewer, for actual evidence support; publish counts/findings and resolve material unsupported claims. Do not report a quality percentage without the sample and method.
6. Produce the local three-part report, one authorized private Telegram test, and a linked correction. Verify partial/ambiguous delivery handling; no production scheduling or VPS claim is required.
7. Negative tests cover missing/stale/conflicting/future evidence, Tier-C-only confirmation rejection, missing validators, source failure, budget exhaustion, injection attempts, unsupported claims, forbidden trade instructions, duplicate/partial delivery, and immutable corrections. CI runs offline and does not depend on third-party availability.
8. GitHub deliverables include readable architecture, setup, tests/CI, licensed/synthetic fixtures, real sanitized run summaries, sample reports, methodology/limitations, and accurate development provenance, including AI assistance. Exclude secrets, proprietary payloads, and unrestricted copying of source news.
9. Independent Reviewer and Release Checker accept the final evidence; CI passes and the PR is human-merged. State separately reports MVP, live observation, and VPS status. Historical replay cannot close a live-observation gate.

## 9. Cadence, Completion, and Change Control

Target reviewable medium work packages. A 25-40 minute interval is a checkpoint, not a hard time guarantee or automatic PR boundary. Estimate remaining work from completed packages and unresolved external dependencies; retire the unvalidated 28-38 hour total.

The first target is research_mvp_complete, not full_blueprint_complete. Long-window Stage 8 observation and Stage 9 release may remain explicitly deferred. No completion status proves predictive value, authorizes advisory services, or grants trading permission. Blueprint changes stay in this file and Git history, with scope decisions and progress in canonical state and the construction log.
