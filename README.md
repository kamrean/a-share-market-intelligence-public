# A-Share Market Intelligence

A read-only market intelligence and strategy research system focused on A-shares,
with lighter coverage of Korean and US markets.

The planned pipeline collects source-linked market data and news, tracks events
and capital flows, and produces evidence-backed daily research reports. Facts,
inferences and conditional scenarios remain distinct. It does not trade or manage
a portfolio, and it does not promise investment returns.

## Current Status

Stage 0 is human-merged in this public repository. P1 covers the Stage 1-2 data
capture and evidence foundation; it does not complete the research product.
See [Blueprint v1](BLUEPRINT.md), [the P1 design](docs/P1_DESIGN.md) and
[canonical state](project-state/state.json) for scope and lifecycle status.

The local foundation includes bounded RSS capture, offline market CSV parsing,
an SQLite provenance store, strict timestamps, domain-specific evidence checks,
immutable reference bundles, retention and hash-verified replay. A fixed five-index
profile remains **BLOCKED_MARKET_SOURCES**: 0/5 benchmarks are live-verified.
This is an unresolved access/rights/implementation gap, not five failed requests.
The [source matrix](docs/SOURCE_MATRIX.md) records candidate sources and limits.
The [probe record](docs/evidence/p1-source-probe.json) records the actual probe
status, including when it has not run. An RSS result cannot satisfy index coverage.

Only one official Federal Reserve press feed is allowlisted for explicit local
retrieval. Market normalization fixtures are synthetic. The paid-provider boundary
is disabled, not a connected integration. Scheduling, research agents, report
delivery, Telegram, trading and VPS deployment remain outside P1.

This repository began as a sanitized import, documented in
[the publication manifest](docs/PUBLICATION_PROVENANCE.json). Historical P0
receipts are preserved unchanged; [P0 merge evidence](docs/reviews/p0-merge.json)
records their subsequent public closeout.

[Canonical state](project-state/state.json) and local review receipts describe
the frozen pre-publication snapshot. `NOT_RUN_AT_SNAPSHOT` is not the live GitHub
status. Subsequent exact-head runs, protection checks and merge evidence belong
on the public pull request. P2 requires P1 human merge and its own reviewed
contract; no later work is authorized implicitly.

## Local Workflows

Prerequisites: Git and Python 3.12. There are no third-party dependencies.
Run from the checkout, using an absolute SQLite **file** path outside every Git
checkout. Use a separate persistent store for real observations so restarting a
process cannot reset that store's daily accounting. Multiple independent stores
are not a system-wide quota service and must not be used to evade limits.

```text
python -m market_intelligence demo --store <absolute-external-demo.sqlite> --task-key demo-1
python -m market_intelligence probe --store <absolute-external-live.sqlite> --task-key probe-1 --allow-network
python -m market_intelligence replay --store <absolute-external-demo.sqlite> --capture-id <capture-id> --parser-version rss-v1
python -m market_intelligence sweep --store <absolute-external-live.sqlite>
```

The demo makes no network requests and labels its authored fixtures
`synthetic_test`. Replay labels records `historical_replay`; it cannot recreate a
past live observation. Probe requires explicit opt-in, current audited permission,
the exact permitted destination and available request budget. It does not follow
feed links, use account credentials, or activate paid providers.

JSON output retains run IDs, UTC times, request/parse denominators, capture IDs
and evidence-gate gaps. Exit code zero means the operation completed, **not** that
market readiness passed; inspect `gate.status`, `mode` and `readiness` together.
Capture IDs identify immutable versions whose metadata contains content and
response hashes; output does not include raw payloads or private store paths.
Reuse the same task key to read a completed result without another dispatch.
Unknown request usage stays reserved and blocks new dispatch for that UTC day.

Limits are two attempts/run, twelve/day/store, one retry, one active lease,
512000 response bytes, ten seconds/attempt and at most 10 MiB for store files.
The Fed raw capture expires after 24 hours. Read paths reject expired payloads;
`sweep` removes eligible raw/normalized content and leaves provenance tombstones.
No background scheduler runs the sweep, so the operator must invoke it.

## Local Validation

Tests are offline and use temporary Git repositories and SQLite stores. Test
transports cannot authorize live verification. CI makes no product HTTP requests.

```text
python -m unittest discover -s tests/governance -p test_*.py -v
python -m unittest discover -s tests/product -p test_*.py -v
python tools/governance.py check
python tools/governance.py prompt --verify
python tools/governance.py bundle
python tools/governance.py gate
git diff --check
```

The active construction branch is `stage1-2-evidence-foundation`. The gate checks
exact scope, staged/worktree agreement, permissions and content-bound receipts.
On detached CI, `--ci` skips local branch identity only.

## Review and Publication

One coordinator and bounded Builders use disjoint file ownership; independent
read-only agents audit the prompt, implementation and release. CI is additional
validation, not independent review. A local `LOCAL_READY_FOR_PR` result is not
remote CI, human merge, source completeness or deployment authorization.

The public target is `kamrean/a-share-market-intelligence-public`. Main requires
pull requests and both named GitHub Actions governance checks, including for
administrators. Force pushes, deletion and auto-merge are disabled. For a sole
maintainer, GitHub required approving review count is zero; independent agent
review and human merge remain procedural gates, not enforcement of merger identity.

Read [the construction entrypoint](START_CONSTRUCTION_PROMPT.md) before changes.
Only named legacy references are consulted; private history, credentials and raw
runtime artifacts are not imported. Public visibility does not add a license.
