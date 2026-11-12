# Construction Entrypoint

Use this repository as the source of truth, not chat history. Work from the
repository root containing this file. The canonical blueprint remains
Market Intelligence System Blueprint v1; do not create a successor version.

## Start and Current Authority

Read AGENTS.md when present, BLUEPRINT.md, project-state/state.json, the active
contract, governance/policy.toml and named review records. Run git status --short,
git log --oneline -5 and git remote -v. Unexpected changes stop work; do not
automatically stash or revert them. Confirm Python 3.12 before using its explicit
local installation if the launcher cannot find it.

The user authorized a clean public repository import, with original evidence
retained privately. The public target is kamrean/a-share-market-intelligence-public.
The private source is provenance only, not the active workspace. Never copy its
.git, refs, old blobs, raw CI logs, credentials or private contact details.
Do not force-push or rewrite either source repository. Only consult source paths
named by the active manifest, not the entire private history on every task.

## Bootstrap and Scope

Before a contract exists, independently review this document and BLUEPRINT.md.
Commit exactly those two reviewed files as the fresh root baseline. Use the
verified GitHub noreply identity, not a personal contact address. Record the
resulting baseline SHA in the later contract, policy, state, checker, tests and
CI; the two root documents must not try to contain their own prospective SHA.

The publication package reconstructs the existing Stage 0 governance foundation,
preserves sanitized source-located construction/review provenance, and obtains
fresh tests, independent review, CI and protection evidence. Prior private merge
evidence is not current public approval. An import is not a new business stage
or a claim that the research MVP is implemented.

Current work is governance-only: exact contract paths, offline checker tests,
CI definitions and publication records. No collectors, product model calls,
Telegram sends, runtime scheduling, VPS mutation, deployment, trading, broker
integration, portfolio control or copied ETF business code.

## Review and Publication Workflow

1. Finalize the contract, policy and state, render its deterministic Builder
   prompt, and obtain independent Prompt Auditor PASS before implementation.
2. Use one coordinator and bounded subagents. Keep one writer per file set.
   Builder cannot act as its own independent Reviewer.
3. Implement only the exact authorized scope. Preserve permission, source,
   scope, secret, staged-content and snapshot validation. Run all offline tests.
4. Finalize decision-affecting documents and log before freezing the snapshot.
   Scan all fresh history, metadata and proposed public text for private details
   and prohibited historical wording. Keep sensitive search values out of logs.
5. Obtain independent read-only Reviewer PASS against actual files and diff.
   Allow one in-scope R0/R1 repair cycle. Further BLOCK, unknown or R2+ findings
   require human escalation; do not change rules to force PASS.
6. Obtain the independent local Release Checker result. Only four exact
   contract-listed attestations may be appended afterwards; changed protected
   inputs invalidate approval. NOT_RUN_AT_SNAPSHOT is not actual GitHub CI.
7. Create only the authorized public repository after privacy and local review.
   Initialize main with the reviewed two-document baseline only. Configure and
   read back branch protection before pushing the reviewed codex branch:
   require PR and both GitHub Actions checks, include administrators, prohibit
   force pushes/deletion, and keep auto-merge off.
8. Use zero GitHub approving-review requirements for the sole-maintainer setup;
   independently recorded agent review and human merge remain required. Do not
   claim this technically prevents an authenticated agent from merging.
9. Open the PR, wait for exact-head CI, inspect resulting public logs/artifacts
   and privacy surfaces, and obtain independent remote Release Checker PASS.
   Record real run/protection results on the PR without changing tested inputs.
   Never merge, bypass protection, or push construction directly to main.
10. After actual human merge, the next package reconciles the public PR/CI/merge
    evidence and creates its own reviewed contract before P1 implementation.

## Continuity and Handoff

Keep Stage0-9 progress in project-state/state.json, with source provenance
distinct from current publication state. Runtime, observation and VPS milestones
remain separate. Free sources do not imply free model execution. Paid adapters
remain disabled until authorization and successful live validation exist.

At interruption, preserve a truthful checkpoint, current hashes, pending results
and next gate. One coordinator plus independent subagents is sufficient; no
permanent multi-window role arrangement or unlimited chat history is required.

Report branch/commit, validation, independent audit/review/release, PR/CI,
protection, merge status, VPS status and next gate. No fabricated observations,
synthetic history presented as live, or unsupported completion claims.
