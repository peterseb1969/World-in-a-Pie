---
section: fixes
cases: [1278]
---

### Integrity checks no longer wait out a dependency that is down

With def-store stopped, `GET /api/reporting-sync/health/integrity` took two and
a half minutes and then timed out, instead of reporting which references went
unchecked. Document-store's and template-store's integrity scans asked def-store
once per reference — and template-store cached nothing — so every reference paid
its own connect timeout, and the aggregator's own budgets expired before either
store answered. A scan now stops asking a store that failed to answer once: the
references after it are recorded as unchecked (`checks_not_run`,
`checks_skipped`), exactly as the failing one was, so the counts and the
`incomplete` / `unknown` verdict are unchanged and the scan answers inside one
timeout. An answer is never treated as an outage: a refusal that carries a code
(template-store's `inheritance_unresolved`) or a 409 ambiguous reference leaves
the rest of the scan asking, and the next scan asks again.

`/health/integrity` also asks template-store and document-store at once instead
of one after the other, so its ceiling is the larger of the two budgets rather
than their sum. Its response shape is unchanged; the services are listed, and
their issues folded, in a fixed order regardless of which answer arrives first.
