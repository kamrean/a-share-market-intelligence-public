# Construction Entrypoint

Work in this repository, not the archived ETF project or private publication source.
Read AGENTS.md, BLUEPRINT.md, project-state/state.json, the active contract and
governance/policy.toml. Read only the named source/design/review records needed
for the active package. The blueprint remains v1.

## Current Package

P0 was human-merged in public PR #1. Its actual merge and successful PR/merge CI
are recorded in docs/reviews/p0-merge.json. Old P0 NOT_RUN_AT_SNAPSHOT receipts
describe historical pre-publication observations; do not rewrite them.

Current P1 covers Stage 1-2: bounded source intake, task records, normalization,
retention/replay and domain evidence gates. Read contracts/p1_sources_evidence.toml
and docs/P1_DESIGN.md. The active branch is stage1-2-evidence-foundation.
New branches must not include the codex/ prefix. Historical names remain intact
in old receipts and commits; do not rewrite history just to rename them.

## Work Sequence

1. Inspect clean Git state, remote and merged prerequisite evidence. Stop on
   unexpected changes; do not silently stash or revert user work.
2. Finalize contract/policy/state and render the exact contract through the
   P1 template. Obtain actual independent entrypoint and Prompt Auditor PASS
   before implementation or product network capture.
3. Use one coordinator plus bounded agents with disjoint write sets. Builders
   cannot independently review their own changes. Preserve all existing guards.
4. Run offline governance/product tests, scope check, prompt verification, review
   bundle and diff checks. Live capture is separately explicit and bounded.
5. Keep SQLite files/raw payloads/private paths outside the repository. Commit
   only safe source-located observations and synthetic/redistributable fixtures.
   A source success is not full benchmark coverage or research acceptance.
6. Freeze source evidence, state, log and all other decision inputs. Obtain
   independent read-only Reviewer and Release Checker verdicts. Allow one
   in-scope R0/R1 correction and one re-review; further/unknown/R2+ BLOCK escalates.
   The user explicitly authorized one additional P1 correction/re-review for
   optional-domain DEGRADED freezing, recorded in repair_exception. Its product
   scope is store.py and test_p1.py only; the default limit remains one. Preserve
   the prior BLOCK in CONSTRUCTION_LOG.md and record two total consumed cycles
   after this repair. Another BLOCK requires a new human decision. No permissions,
   sources, coverage thresholds, merge mode or later-stage authority are expanded.
7. Only four active P1 attestations may be appended after freeze. Old receipts
   stay protected and unchanged. Changed inputs invalidate approvals.
8. Scan commit metadata, public files, descriptions and eventual CI logs for
   credentials/private details. Use the verified noreply identity.
9. Preserve existing public main protection: PR, both app-bound CI checks,
   administrators included, no force pushes/deletion, auto-merge off. Zero
   approving GitHub reviews avoids sole-maintainer self-approval deadlock;
   independent agent review and human merge remain procedural requirements.
10. Push only the reviewed work branch, open one coherent PR, wait for exact-head
    CI and independent remote release verification, then await human merge.

## Effective Boundaries

Only explicitly approved, bounded local read-only source retrieval is enabled.
P1's network allowlist contains the official Fed press RSS endpoint only.
Unknown source rights, missing keys and paid adapters do not permit requests.
Scheduled collection, model execution, Telegram delivery, production operation,
broker/orders, portfolio control, deployment and VPS mutation remain disabled.
No data-access flag authorizes any of those actions.

Keep benchmark targets and missing-source denominators unchanged. Unknown
publication delay/calendars remain unknown, not guessed. Synthetic tests,
historical replay, a live source probe and forward research observation are
different evidence modes. Required market gaps remain BLOCKED_MARKET_SOURCES.

## Handoff

Before interruption preserve actual branch/status, completed and pending checks,
source gaps, snapshot and next gate. Read repository state on resumption.
Report commit, PR, audit/review/release, CI, protection, merge and VPS status.
P2 requires P1's human merge and its own reviewed contract; this entrypoint is
not authorization to skip unresolved data requirements.
