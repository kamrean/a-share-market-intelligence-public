# P4E Retained Evidence Preparation

## Scope and Meaning

P4S was human-merged in PR8 at 1a15840caab444637646e695467496e362f80bbb. Its actual one-shot Fed
observation is immutable. P4E completes a reusable local preparation boundary,
not a new collector, model pipeline or accepted daily report. Existing synthetic
intelligence/research/publication APIs remain unchanged: prepared real Facts
must not be fed to them by changing mode labels. Future integration still needs
an audited contract and valid market/source/method definitions.

All general external permissions stay false. No source request, credential
inspection, subscription/SDK install, model, delivery, scheduler, trading or VPS.
Only an existing fixed local evidence store may be read after independent review
and separate release. Documentation browsing is construction research, not a
product request. The former one-shot capability must stay closed in P4E.

## Source Findings and Remaining Inputs

Independent documentation research01a1e922-af66-7b22-b5be-667b5e35544b did not
establish a compatible selected provider for all five exact targets. It did not
request market data or create accounts. No coverage denominator changes.

- BaoStock documents free API/local analysis for sh.000001 and sz.399001.
  Its API applicability versus website-content restrictions, authenticated
  transport and exact sz.399006 support remain unresolved. Local analysis
  support is not a general external-model or redistribution grant.
- Korean FSC official KOSPI API is a free candidate requiring a portal key,
  with next-business-day publication. Entitlement, retention and downstream
  use remain unresolved. No key is read or requested in chat.
- FRED SP500 is not selected for the SQLite/AI chain; current API restrictions
  and series-specific terms require a compatible alternative or clarification.
  Do not switch access routes to evade terms or substitute an ETF/index.
- Unknown means unverified, not an assertion that private research is illegal.
  A future scope/risk/coverage decision belongs in a reviewed amendment.

Primary documentation:
https://www.baostock.com/mainContent?file=home.md
https://www.baostock.com/mainContent?file=indexData.md
https://www.baostock.com/articleMall/api/contract?contract_status=1&contract_type=5
https://www.cnindex.com.cn/docs/gz_399006_e.pdf
https://www.data.go.kr/data/15094807/openapi.do
https://www.kogl.or.kr/info/licenseType4.do
https://fred.stlouisfed.org/series/SP500
https://fred.stlouisfed.org/legal/
https://www.federalreserve.gov/disclaimer.htm

## Product Boundary

Add market_intelligence/preparation.py and tests/product/test_preparation.py.
Reuse existing models, parsers, path/store verification, P4S metadata
reconciliation, Fact and deterministic news clustering rather than duplicate
a new collector or research engine. All existing product files stay immutable.

The CLI accepts only prepare, with no input path, URL, source, as_of, output
path or text-disclosure override. It reads
Path.home() / ".a-share-market-intelligence" / "p4s-fed-probe.sqlite".
No existence means unavailable; never create a database or directory. Open
SQLite mode=ro and query_only; reject links/hardlinks, unexpected sidecars,
wrong schema/version/identity, oversize, corruption and changed file identity.
Do not initialize Store via its writable constructor. All local handling is
read-only, with no copy/sweep/repair/reset or runtime record creation.

Before materializing ANY raw or normalized payload, inspect only metadata and
check expiry against actual processing time. Exact expiry is expired. An expired
or tombstoned input is unavailable and cannot be rehydrated from a summary/hash.
Recheck expiry before each payload-dependent phase to handle boundary crossing.
Never loosen P4S retention or mutate its capture, attempt, events or summaries.

Bind input to the immutable p4s-source-probe.json using its original protected
snapshot and canonical observation digest. Verify prior run/attempt/capture IDs,
source/locator/parser, original content/response hashes, parsed evidence and
gate/profile consistency. The historical source observation is live_observation;
this new operation is historical_replay. Preserve publication/retrieval/first-seen
times and original as_of; processed_at is actual current time, never a fabricated
source observation. Do not allow replay after payload expiry by using old as_of.

## Preparation Semantics

Prepare typed news Facts in memory with explicit provenance mapping from each
original source Evidence and Capture. Raw capture hash, original Evidence ID
and normalized Fact payload hash have different meanings. Preserve source_mode
and original facts; local processing mode is separate metadata, not relabeling.

Use the original frozen news freshness rule and as_of, not a wider window.
Validate temporal possession, exact Board-origin HTTPS item locators and bounded
plain input. Keep original rejected/stale/conflicting evidence and reason counts;
never silently discard bad rows to report complete coverage. Reuse deterministic
news clustering, retain cluster member IDs, and leave entity resolution unknown.
No entity/sector mapping, upcoming event, empty event day, impact or market fact
may be invented from a title. Titles are untrusted text, not instructions.

At most100 prepared input records and512000 input bytes. Existing parser/gate
limits remain stricter where applicable. Exceeding limits returns unavailable,
not partial unlabelled success. Private in-memory preparation may include
retained titles, but the default CLI and public evidence contain ONLY bounded
IDs, counts, hashes, actual timestamps and known reason codes. No item URLs,
titles, raw text, local paths, environment values or arbitrary error messages.
A content digest binds the prepared object, not truth or independent authenticity.

Preserve original P4S source result degraded, news BLOCK, market0/5 and missing
event/entity/flow/sector readiness. Preparation is not research eligibility or
publication. It creates zero new live-source runs and no delivery/MVP acceptance.

## Approval and Observation

Five current attestations: P4E bundle, validation, review, release and
docs/evidence/p4e-preparation.json. All historical artifacts are immutable.
Actual entrypoint and Prompt Auditor precede product/checker edits. Finalize
state/log before independent Reviewer and separate Release Checker.

preparation-gate requires exact staged/worktree snapshot, full validation,
Reviewer PASS and separate LOCAL_READY_FOR_LOCAL_PREPARATION. probe_gate must
reject the old network operation under this package. All approval role, snapshot,
scope, immutable-history and secret/link checks remain enforced.

Preparation receipt exact outer fields:
schema_version=1, package_id=P4E, snapshot_id, status, processed_at, observation,
reason. Initial NOT_RUN has null processed_at/observation, reason=awaiting_release.
RECORDED contains a strictly validated sanitized preparation result and actual
processed_at, reason=local_preparation. Its result may be prepared or truthfully
unavailable; missing/expired/corrupt input never licenses another request.
The implementation pins a closed result schema and known reasons, count/ref/time
consistency and the original observation identity, with adversarial tests.

Release adds pre_preparation={verdict,snapshot_id,agent_id} and
preparation_digest. Preapproval has null digest; final LOCAL_READY_FOR_PR retains
preapproval and binds SHA256 of canonical preparation receipt. Pending output
cannot close final release. Protected bytes remain frozen after preapproval.

## Validation and Next Gate

Preserve358 old product tests byte-for-byte and107 governance cases/protections.
Tests use isolated authored fixtures/temporary stores and block external calls;
test fixtures are not live evidence. Test exact expiry with SQL traces/parser
spies, whole-file/sidecar immutability, temporal and provenance corruption,
cross-mode confusion, injection-like text, safe unavailable results, counts,
scope, privacy and independent approval binding.

Full offline suites, scope/prompt/bundle/whitespace, independent Reviewer and
separate Release Checker, actual local result, then exact-head Ubuntu/Windows
CI and remote release. One R0/R1 repair total; unknown/R2+ or second BLOCK stops
for human escalation. No retrying reviews to obtain PASS. Human merge only.

After merge, market source selection and real research/report integration remain
open. Model last, private delivery separately gated; no MVP/observation/VPS claim.
