# Construction Entrypoint

Read AGENTS.md, BLUEPRINT.md, project-state/state.json, governance/policy.toml,
contracts/p4b_baostock_live_evidence.toml, docs/BAOSTOCK_LIVE_EVIDENCE.md,
docs/evidence/p4b-user-authorization.json, requirements-baostock.lock,
config/p4b_sdk_manifest.json, docs/reviews/p4m-merge.json and the contract's
named prerequisites. Blueprint stays v1; never access archived/private projects.

## Current Package

P4M PR12 is human-merged. P4B groups pinned optional SDK dependencies, bounded
two-index acquisition, existing daily computation, ephemeral replay and truthful
test/disposal evidence. The user authorized one anonymous session after review
and release, at most12 protocol requests, no retry, and results-only retention.
Market payloads/intermediates must never reach disk/stdout/Git and are discarded
after testing. Keep the durable sanitized quota/result journal. Unencrypted TCP
is explicitly unauthenticated. General permissions and all historical probe
gates remain closed. No model/Telegram/trading/scheduler/VPS or shutdown.

## Work Sequence

1. Verify clean main, PR12 human merge and successful exact-head PR/main CI.
2. Freeze planning, user authorization and SDK lock/manifest. Actual independent
   entrypoint and Prompt Auditor PASS precede product/checker implementation.
3. Disjoint Builders implement only new product/test files. Keep688 old product
   cases/modules/fixtures byte-identical and all160 governance protections.
4. Validate offline, including authored wire responses through pinned SDK;
   full suites, dependency checks, scope/prompt/bundle/diff. Freeze code/state/log.
5. Independent Reviewer, then separate exact-snapshot pre-probe Release and
   market gate. One R0/R1 total; unknown/R2+ or second BLOCK escalates. Never
   inherit old repair exceptions or probe allowances.
6. Perform the single authorized bounded session. Failures/unknown/interrupted
   outcomes consume it. Compose and verify in memory, discard all payloads,
   inspect cleanup evidence, and record only actual sanitized terminal results.
7. Independent final Release, commit/push/one PR, exact-head Ubuntu/Windows CI
   and remote Release. Stop AWAITING_HUMAN_MERGE. Never merge or bypass.

## Next Gate

Human merge after exact-head CI and independent remote release; resolve remaining anonymous sources and genuine model/report/private-delivery acceptance. The one-shot market allowance is consumed after any reserved attempt and never authorizes retries, scheduling or VPS.
Observed acquisition/input coverage is separate from authenticated data, accepted
five-index coverage, genuine live research and MVP completion. Keep gaps explicit.
Final report always includes prior completed, this-package completed and remaining
work, as well as branch/commit/PR/audits/validation/CI/merge/VPS/next gate.
