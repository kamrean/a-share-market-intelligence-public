# P4D Protocol Recovery

P4B PR13 is human-merged and its exact-head Ubuntu/Windows CI passed. Its one
actual session failed after one request and 88 wire bytes, before any validated
response. The original payload was deliberately not retained. That result does
not identify a server defect or a specific local parser defect. Its allowance
is permanently consumed; the P4B journal and evidence remain unchanged.

The installed, hash-pinned SDK parses plain responses without validating their
CRC, declared length or version equality. Our wrapper added those checks.
SDK request formatting and authored tests are not independent evidence that
server responses obey every added rule. P4D first measures the failure point
without changing any accepting rule or retaining response content.

## One Package, Two Checkpoints

The user separately authorized two sessions, at most nine requests altogether.
The planned sequence uses one login request, then seven requests for the two
indices. Each is reserved durably before dispatch; failure or interruption never
refunds it. The extra numerical capacity is not authority for an eighth
verification request, retry, reconnect, third session or automatic transition.

The diagnostic schema is closed: stable first-failure enum, header/version/type
categories, bounded lengths, UTF-8/CRC/terminator check states and disposal
booleans. It never contains raw strings from the server, names, prices or error
messages. Unreached and indeterminate checks must not be labeled PASS. Candidate
body lengths describe a documented local split, not a certified server format.

Only an evidence-supported compatibility repair may precede the second review
and verification. Unknown or insufficient evidence stops dispatch. Diagnostic
approvals bind their original snapshot and are frozen as inputs before the
changed implementation is reviewed. They cannot approve later code by reuse.

Use the existing pinned SDK, existing guarded SQLite/results-only patterns and
unchanged P4B memory composition. No new business engine, payload store, paid
source, key, endpoint, scheduler, model, Telegram, broker or VPS work. All payload
references are discarded; no secure RAM-erasure or OS-paging guarantee is made.

## Verification and Evidence

The contract is the sole detailed scope/validation list. The generated Builder
prompt references it rather than embedding duplicated historical documents.
One entry review can cover both entrypoint and prompt roles with distinct
verdicts. Product Reviewer and Release Checker remain independent roles.
Final state/log are frozen before final review; actual results then belong in
the named attestations. Every recorded test identifies its actual snapshot.

Final reporting includes actual requests, outcomes, observed dates, latest
expected provider session, received/accepted/rejected rows and duplicate
outcomes, plus cleanup. A passed engineering PR may still retain a failed source
result. Two observed indices cannot establish five-index coverage, live model
research, delivery, or research-MVP completion.
