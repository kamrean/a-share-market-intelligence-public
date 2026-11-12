# A-Share Market Intelligence

A read-only market intelligence and strategy research system focused on A-shares,
with lighter coverage of Korean and US markets.

The planned pipeline collects source-linked market data and news, tracks events
and capital flows, and produces evidence-backed daily research reports. Facts,
inferences and conditional scenarios remain distinct. It does not trade or manage
a portfolio, and it does not promise investment returns.

## Current Status

Only the Stage 0 governance foundation is implemented. Data collectors, research
agents, report delivery and VPS deployment are not implemented or authorized by
this package. See [Blueprint v1](BLUEPRINT.md) for scope and acceptance criteria.

The foundation was merged in the original private repository. This repository is
a fresh, sanitized import with its own reviews, CI and human merge gate. Source
provenance is in [the publication manifest](docs/PUBLICATION_PROVENANCE.json).
Historical source approval is not public-import approval.

[Canonical state](project-state/state.json) and local review receipts describe
the frozen pre-publication snapshot. `NOT_RUN_AT_SNAPSHOT` is not the live GitHub
status. Subsequent exact-head runs, protection checks and merge evidence belong
on the public pull request. P1 cannot start before that PR is human-merged and its
own contract is approved.

## Local Validation

Prerequisites: Git and Python 3.12. No third-party dependency is required by P0.
Tests are offline and use temporary Git repositories.

```text
python -m unittest discover -s tests/governance -p test_*.py -v
python tools/governance.py check
python tools/governance.py prompt --verify
python tools/governance.py bundle
python tools/governance.py gate
git diff --check
```

The active construction branch is `codex/publication-stage0`. The gate checks
exact scope, staged/worktree agreement, permissions and content-bound receipts.
On detached CI, `--ci` skips local branch identity only.

## Review and Publication

One coordinator writes; independent read-only agents audit the prompt, implementation
and release. CI is additional validation, not independent review.

The public target is `kamrean/a-share-market-intelligence-public`. Main requires
pull requests and both named GitHub Actions governance checks, including for
administrators. Force pushes, deletion and auto-merge are disabled. For a sole
maintainer, GitHub required approving review count is zero; independent agent
review and human merge remain procedural gates, not enforcement of merger identity.

Read [the construction entrypoint](START_CONSTRUCTION_PROMPT.md) before changes.
Only named legacy references are consulted; private history, credentials and raw
runtime artifacts are not imported. Public visibility does not add a license.
