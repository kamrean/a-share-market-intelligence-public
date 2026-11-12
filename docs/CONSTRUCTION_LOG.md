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
