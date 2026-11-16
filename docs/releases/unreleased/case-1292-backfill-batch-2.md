---
section: fixes
cases: [1292]
packages: [wip-auth]
---

### A terminology identifier that names several terminologies is reported as ambiguous, not as an outage

When a bare value resolves to several terminologies, for instance one that two
terminologies hold as synonyms under different qualifiers, Def-Store answers
409 with the candidates. template-store and document-store read that answer as
a failure to reach Def-Store:
- a template's validation reported the reference as `unverifiable`;
- a document naming the terminology was refused as a Def-Store outage.

Both now report `ambiguous_reference` with Def-Store's candidates. A
template-store request where nothing catches it answers 409, not 503. The
template integrity scan records such a reference as a skipped check with that
reason. Name the terminology by its canonical id or `ns:VALUE`.

### Smaller fixes from the commit-review backfill

- **Migrating documents without a namespace.** An administrator who omits the
  namespace gets the 422 straight away. Before, the Registry was asked once for
  every namespace first.
- **Permission answers.** A Registry answer whose `permission` is not one of
  the four levels counts as no answer: the request gets 503 and nothing is
  cached. Before, the value was cached for 30 s as a denial. The 503 text no
  longer says the Registry "could not be reached" when it answered.
- **A blank `WIP_AUTH_REGISTRY_URL`** counts as unset, for permission checks
  and resolution alike.
- **A key check the key list keeps changing under** stops after three bcrypt
  sweeps and answers 503 (retry). Before, it swept again without bound.
- **An administrator's readable namespaces** are the active and archived ones.
  A legacy `status: "deleted"` record no longer counts.
- **The reconcile-synonym-lifecycle endpoints** now say that an entity revived
  while the pass ran gets its synonyms back without `revive=true` as well.
- **The anonymous, rate-limit-exempt, always-fresh `/api/<svc>/health`** is
  kept as it is. Its cost is now documented in `wip_auth.health_cache`.
