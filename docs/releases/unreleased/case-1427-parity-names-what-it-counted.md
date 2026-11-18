---
section: new
cases: [1427]
packages: [wip-client]
---

### The parity answer now says whether it compared rows

`GET /api/reporting-sync/parity?include_counts=false` checks table shape and
never looks at a row, but nothing on its answer said so: a namespace whose
documents had never synced could read `verified` with no reasons, while
`verified` is documented as "every check ran and found nothing missing". The
answer now carries `counts_included` — `true` from the default form, `false`
from the structure-only one — and the route and MCP tool wording scope
`verified` by it. The verdict itself is unchanged: a limit you asked for is not
an outage, and `unverified` keeps meaning a check that could not run.

**Action:** none required. Callers of the cheap form can treat
`counts_included: false` as "shape verified, rows unknown" and ask with counts
for the rest; `@wip/client` types the field on `NamespaceParityResult`.
