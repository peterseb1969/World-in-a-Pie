---
section: action-required
cases: [512]
packages: [wip-auth]
---

### A key scoped to one namespace plus a read-only `wip` derives its namespace

Omitting `namespace` derived it only for a key scoped to exactly one namespace.
Every app key the deployer gives an app is scoped to the app's namespace plus
`wip` (read, for the shared vocabularies), so no app key derived anything, and
apps had to pass `namespace` on every call.

A key whose scope is exactly one namespace plus `wip` now derives that one
namespace when its permission on `wip` is read. The services ask the Registry for
that permission (a grant made through the API counts), cached like every
permission read; when the Registry cannot be asked the request answers 503. A key
that holds write or admin on `wip` derives nothing, so omitting `namespace` never
picks between two namespaces it can write. Scopes of any other shape behave as
before. The same rule decides every door that takes an omittable `namespace`:
identifier resolution in all stores, the terminology import's target, term
search, and document-store's template facets, impact stats and migrate (where,
as before, a platform administrator always names the namespace).

**Action:** for such a key, a bare value now resolves in its own namespace, as
for a single-namespace key. An app that names a `wip` terminology or template by
bare value without passing `namespace` gets a 404 where it used to pass the
value through: name it `wip:VALUE` or by id, or pass `namespace=wip`.

`wip-auth`: `implied_namespace(identity)` is the rule;
`unresolved_detail`, `field_unresolved_detail`, `resolution_miss` and
`miss_detail` are now coroutines (await them).
