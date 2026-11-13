# Construction Log

## Historical Source Foundation (Sanitized Summary)

The original private foundation was merged on 2026-11-12 in PR #1 at
`b4754fc1a8bd30b93002c9be338e103897b1b71e`. Its final tested head was
`32ed475eaf3daeefcfb23b6a3594bbfb9dd5a8e6`.
The source contained the reviewed Blueprint v1, narrow P0 contract, permission
policy, lifecycle registry, offline checker/tests and independent review receipts.

An initial prompt repair and a later Windows CI failure both occurred. The latter
was a test fixture path-normalization mismatch, not permission to weaken the
production junction guard. The user authorized one bounded correction; the fix
added canonical/alias path cases and a real Windows junction regression. The
corrected source passed 50 local tests and Windows/Linux CI, independent review
and release review before human merge.

Exact source hashes, review identities, failure/success run IDs and private
evidence links are preserved in PUBLICATION_PROVENANCE.json. The original private
repository remains the full audit source. This is a sanitized historical summary,
not a transcript and not an approval of this new public snapshot.

## Public Import - Frozen Pre-Publication Record

The user authorized public visibility with branch protection and expressly
allowed a fresh repository to avoid leaking old history. The new target is
`kamrean/a-share-market-intelligence-public`; the private source is unchanged.

The independently reviewed Blueprint v1 and portable entrypoint form the fresh
two-document root `2f4539567980cedc28b605c2c27a84ed5fcad96f`. Current selected source files
are imported without any original Git objects, refs, raw logs or private contacts.
Contract, policy, state, checker, tests and CI are rebound to this root and public
identity. Only one protected provenance manifest is added to the original scope.

All product permissions remain disabled. This package is publication/governance,
not new business implementation. Original independent verdicts are retained only
as provenance; new local receipts must come from actual fresh audits and tests.

At this frozen point the public package is implemented but unreleased. Its
validation, Reviewer and Release Checker results are recorded in the four named
attestations after they actually finish. Those receipts do not grant authority.

After local PASS, the coordinator may initialize remote main with only the
reviewed root, apply and read back branch protection, then push the reviewed
construction branch and open a PR. Actual head-specific CI, privacy and protection
verification is recorded on that PR. Do not rewrite this snapshot to claim later
events, direct-merge, bypass protection or start P1 before human merge.

## P1 Stage 1-2 - 2026-11-13

The user confirmed merge and authorized the next Stage1-2 package, with new
branch names omitting the codex/ prefix. Historical P0 names are unchanged.
Public PR #1 was actually merged at 94ae20ca0ea225aa8118314e04684869accb2a11;
its exact-head and merged-main CI runs succeeded. Evidence is preserved in
docs/reviews/p0-merge.json. The source private and archived ETF projects are
not construction targets.

P1 keeps the five named benchmark targets and an honest 0/5 live-verified
coverage denominator. Only an explicitly permitted official press RSS probe
is authorized. Account/key, use and retention gaps are not fabricated as
failed network requests or completed integrations. Engineering readiness is
separate from live-market research coverage and full MVP acceptance.

The independent entrypoint review passed. Initial Prompt Auditor returned BLOCK
because policy incorrectly declared product_runtime_implemented before any
runtime code existed. The single permitted R0 correction removed that factual
claim from policy; state remains the implementation-status authority. No
permissions or scope changed. This consumes the one P1 repair cycle; it is not
erased or reset for later review. The corrected Prompt Auditor returned actual
PASS before any product implementation or capture. The new entrypoint and audit
receipts retain the independent agent identity and exact input hashes.

The bounded Builder completed seven runtime modules, two source/profile configs,
an empty third-party dependency lock, two authored synthetic fixtures and the
product test suite. The coordinator updated governance, scope/immutability tests,
CI, source documentation and this lifecycle record. Both suites passed locally:
54 governance tests and 55 product tests on Python 3.12.10. The coordinator also
ran the actual demo, replay and sweep command entrypoints using external stores.
Synthetic demo passed its synthetic profile; replay was explicitly historical.
These results do not claim live-market coverage or independent review.

The permitted real feed observation ran from 2026-11-13T00:51:36.953865Z to
2026-11-13T00:51:37.312286Z, run eb81a0502a3b4d1eb657a3f7a3de5034. One request
received 14801 bytes and parsed 20/20 records, with zero transport failures or
unknown usage. Reusing the same task key returned the same run without a new
request. The source is live_verified for this bounded observation only; quality
rates requiring further observations remain null. The evidence gate is BLOCK:
market coverage remains 0/5 and the news sample includes stale evidence. No
freshness rule or denominator was relaxed. The safe summary and content/response
hashes are in docs/evidence/p1-source-probe.json; raw bytes remain outside Git,
expire after 24 hours and are unavailable for replay after expiry.

P1 is implemented_unreleased, not completed research or source acceptance.
The source selection matrix remains explicit about all five unavailable index
integrations; no market requests, account keys or paid adapters were used. Public
content/privacy prechecks passed. Final independent verdicts belong in the four
active P1 attestations after they actually finish, with exact-head remote results
on the PR. Human merge, unresolved source gaps and a separately reviewed P2
contract remain gates. No deployment, VPS, scheduling, models or delivery ran.

## Authorized P1 Repair Exception - 2026-11-13

The user explicitly authorized exactly one additional bounded repair and re-review
for optional-domain DEGRADED results that could not be frozen. Default repair
budget remains one; the named contract/policy exception adds one for this defect
only. Total consumed cycles after correction are two, not a reset to one.
Product edits are limited to store.py and test_p1.py. Contract/entrypoint/policy,
checker/tests, generated prompt and review records are aligned only as needed to
record that authorization. No source, permission, coverage or merge boundary changes.

The first implementation Reviewer reproduced a legitimate DEGRADED failure:
recomputing only accepted records dropped rejected stale-news provenance. Both
original offline suites passed but lacked the evaluate-to-freeze regression.
The coordinator paused and preserved BLOCK; the user first requested explanation,
then explicitly authorized this correction. The original verdict and prerequisite
receipts follow verbatim as historical objects. They approve no changed inputs.
New audited inputs and final verdicts require actual new independent reviews.

### Historical p1-review

```json
{
  "schema_version": 1,
  "snapshot_id": "88b9de82cd67ac3c9653170902ef7416a44e2cc160ae9b046153ac5760874c3d",
  "role": "reviewer",
  "verdict": "BLOCK",
  "agent_id": "01a1d265-dc4c-73a1-9eaa-79f1afd1dd4f",
  "read_only": true,
  "findings": [
    {
      "severity": "P2",
      "path": "market_intelligence/store.py",
      "line": 455,
      "title": "Authorized optional-domain DEGRADED cannot be frozen",
      "description": "freeze() recomputes only accepted_ids, discarding stale optional rejected-input provenance; strict domain equality then rejects a legitimate DEGRADED result.",
      "reproduction": "In a temporary store run demo(), read complete synthetic evidence, and set only the news rule to required=false, allow_degraded=true, max_age_seconds=1. evaluate() returns DEGRADED; freeze() raises 'gate domain details do not match durable evidence'.",
      "required_fix": "Validate and preserve rejection provenance without putting rejected evidence into usable references; add evaluate-to-freeze regression coverage without weakening fabricated-PASS protections.",
      "next_gate": "HUMAN_ESCALATED: the one P1 repair cycle is consumed; explicit bounded authorization is required before another repair/re-review."
    }
  ],
  "repair_cycles": 1
}
```

### Historical p1-prompt-audit

```json
{
  "schema_version": 1,
  "role": "prompt_auditor",
  "agent_id": "01a1d23c-5b4e-7af0-98d5-87b97c44f2e0",
  "verdict": "PASS",
  "independent": true,
  "read_only": true,
  "repair_cycles": 1,
  "findings": [],
  "files": {
    "contracts/p1_sources_evidence.toml": "416c436d6a14eab998fa9802610f5e6b310f8271dca43f6216dd84407462910c",
    "governance/policy.toml": "9fb18a82ec5865fa3b4f8607f8ca1b6ee6baaca3ab0aea18bcb5b8336a65ca85",
    "templates/p1_builder_prompt.md": "7033af6d734b6739f9df270d0ccf5769f83f0a7e0226073a7c767b4923450e66",
    "docs/reviews/p1-builder-prompt.md": "0280bc8e6753651a3fe46b0a6155279f483d91b40915a034939a3bd8b239968d",
    "AGENTS.md": "d8ce46b22efbd879ebd0879c0ba7a7d4e4fe7f1f860538866672122dfec4d3e1",
    "docs/P1_DESIGN.md": "38b7862aafb76172da7e347757f5639583efc2aa80fc5d4bf40e43b75ea4aa91"
  }
}
```

### Historical p1-entrypoint-review

```json
{
  "schema_version": 1,
  "role": "blueprint_reviewer",
  "agent_id": "01a1d23c-5b4e-7af0-98d5-87b97c44f2e0",
  "verdict": "PASS",
  "independent": true,
  "read_only": true,
  "repair_cycles": 0,
  "baseline": "94ae20ca0ea225aa8118314e04684869accb2a11",
  "files": {
    "BLUEPRINT.md": "030a634be17b87c594a3eeb95b665f870ea8da207567d0a91a20950695853d12",
    "START_CONSTRUCTION_PROMPT.md": "cfd5c8646d73d493af334db5d94f01ebadab03c2ee7bef06c3d583a6c158432f"
  },
  "scope": "P1 entrypoint consistency; separate Prompt Auditor approval required."
}
```

### Repair Implementation and Validation

The independent entrypoint and Prompt Auditor approved the named exception.
Only trailing extra blank lines were then removed from contract/policy/log;
the auditor independently verified that formatting-only change and refreshed
the exact hashes before product edits. This was not a new defect repair cycle.

The coordinator added evaluate-to-freeze regression cases before changing the
store. The pre-fix test run (61 tests) reproduced stale/conflicting optional-news
freeze failures and the missing rejected-provenance validation. The bounded fix
reloads accepted and rejected input IDs for non-BLOCK results, validates their
durable hashes/retention and recomputes the complete result. A full-result match
is required; only accepted records become usable references. Existing stricter
BLOCK recording remains unchanged. No threshold or source configuration changed.

Seven new product tests cover stale optional evidence and reopen/refreeze,
missing/fresh controls, conflicting rejections, excluded Tier C on PASS,
forged/omitted/unknown/duplicate rejection claims and fabricated PASS, corrupted
rejected-record hashes, and retention expiry. Final local results are 55/55
governance tests and 62/62 product tests PASS on Python 3.12.10. No live product
request, deployment, VPS operation or delivery occurred during this correction.
The original real probe and 0/5 benchmark gap remain unchanged.

This consumes the explicitly authorized second P1 correction cycle. A further
formal BLOCK requires another human decision, not automatic repair. Independent
implementation re-review and release verdicts are pending at this protected
freeze; their actual results belong in the four active attestations. Remote CI
and the final human-merge gate remain separate, to be recorded on the PR.

## P2 Stage 3-4 - 2026-11-13

The user confirmed PR #2 merged and authorized the next blueprint work package,
with actual independent review/release/PR/CI and a final human-merge stop.
Clean main was synchronized to e7b36fb715a508c1e4ca91ac97a06b40bed2da7a.
PR #2 merged at 2026-11-13T05:46:19Z; exact-head PR CI 43363252300 and merged-main
CI 43374192450 both succeeded. The prior full gate passed on merged main.
docs/reviews/p1-merge.json records those observations without rewriting old
pre-publication records. P1's pending wording above is historical, not a current
claim that its independent review or merge remains pending.

P1 Stage1 engineering is merged with unresolved 0/5 live benchmark coverage;
Stage2 core is merged. P2 does not waive source or full research acceptance.
The active package covers offline market/flow/sector methods, news ranking,
entity mapping and sourced event lifecycle/domain validation. All product
external access is disabled. P1 runtime, source configuration and historical
reviews stay immutable; only its test fixture isolation may change for P2.
Default one R0/R1 correction applies anew to this package. P1's exceptional
second correction is retired historical authorization, not inherited by P2.

Independent entrypoint and Prompt Auditor PASS preceded implementation. The
coordinator owns governance/docs/integration; one bounded Builder owns the eight
named product/config/fixture/test paths. Runtime modules from P1 are unchanged.
Only explicitly synthetic offline execution is allowed. Validation and final
independent verdicts will be recorded after actual completion, not prefilled.

The bounded Builder delivered three pure modules, two pinned configs, an authored
synthetic fixture and its tests. P1 product runtime/storage stayed unchanged.
P1 test isolation now uses an explicitly test-only audited policy tree; active
P2 policy still denies dispatch before reservations or transport. The coordinator
re-ran the final product suite: 102/102 PASS (64 P1, 38 P2). Earlier 95-test runs
preceded seven final boundary tests and do not approve the final bytes.
The complete governance suite also passed 56/56 tests, retaining index/worktree,
scope, immutable-history, secrets, Windows junction and independent-role guards.

The final demo actually ran at 2026-11-13T09:47:16.318784Z, run
41dc4bef3d55429d81a2373e9121fe27, with historical synthetic
as_of=2020-01-10T12:00:00Z. Deterministic bundle ID is
f4c8c13da591b461976b76939c362a2f7039600116daf7dc95dd4f79c099781d.
It has 40 usable references. Market/sector/news/entity/event domains PASS under
the synthetic profile; optional unintegrated flow is DEGRADED, making the global
result DEGRADED. Live benchmark coverage remains 0/5 and readiness remains
BLOCKED_MARKET_SOURCES. The actual envelope is in docs/evidence/p2-demo.json;
it is not a real source observation, production calendar or research run.

P2 is implemented_unreleased, with zero formal repair cycles consumed. New
source/model/delivery/VPS/trading permissions were not granted or used.
Independent implementation review, release and remote CI/human merge remain
separate gates. Final verdicts are appended only after actual completion.

### P2 Single R0/R1 Repair

The first independent Reviewer returned BLOCK on the frozen snapshot below.
It found late bar versions could enter reaction calculations (R1), and the
coordinator's JavaScript JSON reserialization changed float scores to integers,
invalidating the committed sample's Python canonical hash (R0). The initial
snapshot and review are historical; they do not approve corrected bytes.

```json
{
  "schema_version": 1,
  "role": "reviewer",
  "verdict": "BLOCK",
  "snapshot_id": "0f78cc49120da94d48a88dc255dc0465f1066b5814c81953abf76bb9d8bc5afb",
  "agent_id": "01a1d450-4973-7ec0-b10c-c1555a6082bc",
  "read_only": true,
  "repair_cycles": 0,
  "findings": [
    {
      "severity": "P2",
      "classification": "R1",
      "path": "market_intelligence/news_events.py",
      "line": 298,
      "description": "Reaction calculations check closes but accept bar publication/retrieval/first-seen versions later than the reaction assertion. Validate every referenced version against the assertion and regress both publication and possession."
    },
    {
      "severity": "P2",
      "classification": "R0",
      "path": "docs/evidence/p2-demo.json",
      "line": 787,
      "description": "JavaScript reserialization changed float scores to integers, invalidating the Python canonical bundle hash. Preserve actual CLI numeric representations and verify the committed sample."
    }
  ]
}
```

Within the contract's one correction cycle, every referenced start/end bar and
event-confirmation version
version now checks publication against the assertion's publication, retrieval
against assertion retrieval and first-seen against assertion first-seen.
Regression cases first failed
on both endpoint sessions, then passed with market facts still valid but the
unsupported reaction/event rejected. Equal-boundary and unknown-publication
controls and equivalent duplicate versions are covered. No timing requirement
was weakened or inferred from session close alone.

The sample was regenerated from the actual CLI, preserving its raw JSON text
without JavaScript number conversion. Current real envelope run is
24e0292440d64733b6a054e8ff0d3460 at 2026-11-13T10:02:35.905281Z;
fixture as_of and the deterministic f4c8c13d... bundle ID are unchanged.
A new test verifies the committed sample's hash and complete recomputation.

During repair self-validation, an old P1 first-seen test intermittently failed:
two consecutive Windows clock reads could coincide, so its intended later
capture was not later. The R0 fixture now explicitly makes the first capture one
second earlier; rejection/preservation assertions and all P1 runtime bytes are
unchanged. This is test-evidence stabilization within the same correction batch,
not a permission change or a second formal repair/re-review.

Final product self-validation passed 108/108 tests (64 P1, 44 P2). The full
governance suite was re-run and passed 56/56 tests after correction. P2 has now
consumed one formal repair cycle. The initial zero-cycle record above describes
the pre-BLOCK freeze only. Further formal BLOCK requires human escalation.
A fresh protected snapshot, validation and independent re-review/release are
required; old approvals and the malformed sample are not reused.
The repair also tests distinct assertion first-seen/retrieval times so a later
bar cannot backfill knowledge at an earlier assertion first-seen timestamp.
The same check covers later event-confirmation versions; its regression also
failed before the correction and passed afterwards within this same batch.
