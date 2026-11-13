# A-Share Market Intelligence

A read-only market intelligence and strategy research system focused on A-shares,
with lighter coverage of Korean and US markets.

The planned pipeline collects source-linked market data and news, tracks events
and capital flows, and produces evidence-backed daily research reports. Facts,
inferences and conditional scenarios remain distinct. It does not trade or manage
a portfolio, and it does not promise investment returns.

## Current Status

P0, P1 and P2 are human-merged in this public repository. Stage1 engineering
retains source gaps; Stage2 core and Stage3-4 offline intelligence are merged.
Current P3 work covers the Stage5-6 evaluation and research committee foundation.
It does not complete the research product or verify live model integration.
See [Blueprint v1](BLUEPRINT.md), [P3 methods](docs/P3_METHODS.md) and
[canonical state](project-state/state.json) for scope and lifecycle status.

The local foundation includes bounded RSS capture, offline market CSV parsing,
an SQLite provenance store, strict timestamps, domain-specific evidence checks,
immutable reference bundles, retention and hash-verified replay. A fixed five-index
profile remains **BLOCKED_MARKET_SOURCES**: 0/5 benchmarks are live-verified.
This is an unresolved access/rights/implementation gap, not five failed requests.
The [source matrix](docs/SOURCE_MATRIX.md) records candidate sources and limits.
The [probe record](docs/evidence/p1-source-probe.json) records the actual probe
status, including when it has not run. An RSS result cannot satisfy index coverage.

P1's original official Federal Reserve press observation remains historical.
Active P3 policy disables all external retrieval, including the old probe command;
existing runtime guards deny dispatch before network access. Market normalization
fixtures are synthetic. The paid-provider boundary is disabled, not connected.
Scheduling, live models, reports/delivery, Telegram, trading and VPS remain
outside P3. Synthetic role adapters are explicitly simulated, not live-connected.

This repository began as a sanitized import, documented in
[the publication manifest](docs/PUBLICATION_PROVENANCE.json). Historical P0
receipts are preserved unchanged; [P0 merge evidence](docs/reviews/p0-merge.json)
[P1 merge evidence](docs/reviews/p1-merge.json) and
[P2 merge evidence](docs/reviews/p2-merge.json) record actual public closeout.

[Canonical state](project-state/state.json) and local review receipts describe
the frozen pre-publication snapshot. `NOT_RUN_AT_SNAPSHOT` is not the live GitHub
status. Subsequent exact-head runs, protection checks and merge evidence belong
on the public pull request. P4 requires P3 human merge and its own reviewed
contract; no later work is authorized implicitly.

## Research Foundation

P3 adds strict claim provenance and a fixed researcher/reviewer/editor protocol.
At most two research/review rounds are permitted. Every selected claim needs
an exact-content review; the editor cannot introduce or rewrite approved claims.
Live adapters remain disabled and never fall back to fabricated successful runs.

A bounded external SQLite budget store reserves calls and token allowances
before dispatch, persists run/day accounting across restarts and fails closed
on unknown usage. This is not a distributed quota service or exact dollar billing.
The demonstration uses an ephemeral store and authored synthetic responses:

```text
python -m market_intelligence.research demo
```

Evaluation separates citation validity from actual semantic support and keeps
missing entailment/usefulness assessments unassessed. T+1/T+3/T+5 calculations
use explicit mapped-market sessions, a pinned anchor and benchmark, and disclose
missing or immature outcomes; descriptive returns do not demonstrate profit.
The [P3 methods](docs/P3_METHODS.md) define denominators and limitations.
The [actual demo sample](docs/evidence/p3-demo.json) is synthetic engineering
evidence, not a model-quality test or a genuine observation window.

## Local Workflows

The P2 offline command uses fixed authored fixtures, reads no credentials and
makes no network requests or persistent writes:

```text
python -m market_intelligence.intelligence demo
```

Its envelope separates actual `run_id` / `generated_at` from historical fixture
`as_of`. The content-addressed result is under `bundle.content`; inspect `status`,
the six `domains`, `live_market_coverage` and `readiness` together. A synthetic
DEGRADED result is expected when optional flow is unavailable. The committed
[demo record](docs/evidence/p2-demo.json) identifies actual execution status.
Verify bundles with `verify_bundle` and the original inputs/configuration. Keep
raw CLI JSON when exporting evidence: numeric representations participate in
the Python canonical hash and must not be coerced by another runtime.

Methods include explicit-universe breadth and turnover, equal-weight sector
returns and rank change over complete declared session windows. Missing history
is `insufficient_history`. Source-linked news groups retain all member IDs and
visible ranking factors; mapping ambiguity stays unresolved. Event states never
infer confirmation from elapsed time. Outcome, expected impact and measured
reaction are separate. See [the method boundaries](docs/P2_METHODS.md) and the
versioned configuration for formulas, universes and limitations.

Sector rotation compares equal-weight returns over the last L sessions with the
preceding nonoverlapping L-session window; it needs 2L+1 closes for every declared
member. Relative turnover divides the current universe total by the mean of the
previous L totals. Rank ties use sector IDs; zero/missing baselines remain null.
News priority uses authority points A=4/B=2/C=0, plus one for unambiguous mapping,
times step decay 2^(-floor(age/half_life)). It is not a truth/profit probability.

Prerequisites: Git and Python 3.12. There are no third-party dependencies.
Run from the checkout, using an absolute SQLite **file** path outside every Git
checkout. Use a separate persistent store for real observations so restarting a
process cannot reset that store's daily accounting. Multiple independent stores
are not a system-wide quota service and must not be used to evade limits.

```text
python -m market_intelligence demo --store <absolute-external-demo.sqlite> --task-key demo-1
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

The active construction branch is `stage5-6-research-foundation`. The gate checks
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
