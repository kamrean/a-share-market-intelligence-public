# Legacy Reuse Inventory

Inventory date: 2026-11-12. Source repository:
kamrean/a-share-sentinel (archived, private audit reference).
Observed source HEAD: d2a00872d7ea388e12c89f40d0475b85c517b866.
Only bounded read-only inspection was performed. No source file was copied.
The user considers the ETF project archived; this manifest does not claim that
all legacy canonical lifecycle documents were themselves updated to archive.

| Source path at pinned commit | Classification | Reusable idea and compatibility decision | Target |
| --- | --- | --- | --- |
| tools/check_stage_scope.py | rewrite_pattern | Compare committed/staged/worktree/untracked changes with a contract. Old schema, trading paths and lifecycle imports are incompatible; implement a small new checker. | P0 |
| tools/build_review_bundle.py | rewrite_pattern | Assemble actual scope and validation evidence for a read-only review. Add content-bound snapshots; do not copy old schema/imports. | P0 |
| tools/render_stage_prompt.py | rewrite_pattern | Deterministic contract-to-prompt rendering. Replace old multi-template/stage schema with one TOML-to-JSON template. | P0 |
| templates/reviewer_prompt.md | reference_only | Preserve read-only review and evidence-backed verdicts. Hardcoded Stage3/4 lifecycle, safe-zero and trading references prevent direct reuse. | P0 review design |
| .github/workflows/ci.yml | reference_only | Offline tests and artifact rejection are useful ideas. Existing job installs business dependencies and builds Docker; do not import it. | P0 CI definition |
| project-memory/workflow_state.json | forbidden | Legacy authority and stage progress cannot authorize this project. Path identified only; historical state is not migrated. | none |
| project-memory/stage-contracts/ | forbidden | ETF lifecycle/permission semantics belong to the archived system. Do not bulk read or import. | none |
| apps/ and deploy/ | forbidden | Trading/operational integration is outside P0 and this new read-only MVP. No source-code audit or compatibility claim is made for these trees. | none |
| credentials, logs, reports, databases, session/cookie/runtime artifacts | forbidden | No copying, ingestion, publication, or implied license. No credential content inspected. | none |

No candidate is marked directly pluggable. The new tools are freshly authored
against the new contract. Later packages may add a named, reviewed reuse decision
only when actual source inspection and compatibility checks justify it.
