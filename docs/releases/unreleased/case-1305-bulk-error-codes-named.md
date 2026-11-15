---
section: fixes
cases: [1305]
packages: [wip-auth]
---

### The served bulk error surface names the codes the services actually emit

`error_code` on a bulk result item is documented in one place
(`libs/wip-auth`), and every service's OpenAPI spec publishes that text. It
named two codes no service ever returns — `archived` and
`archived_relationship_endpoint` — and left out six it does:
`ambiguous_terminology`, `dependency_unavailable`, `duplicate_in_snapshot`,
`exists_inactive`, `identity_hash_stale`, `unknown_successor`. The two stale
names are gone and the six are listed, each with what it means and where its
details are (`dependency_unavailable` carries `details.dependency`).

No behaviour changed: these codes were already returned, just undocumented, so
a caller branching on `error_code` handled them as unknown. A caller that
branched on `archived` never matched anything and still matches nothing.

The `details` description tied the compatibility diff to `on_conflict=validate`
for every create. Only a terminology or term re-create reports it that way; a
template create reports the diff on the `updated` item it writes, whatever
`on_conflict` says. The wording now splits the two.

**Action:** none — add the new codes to any client that enumerates them.
