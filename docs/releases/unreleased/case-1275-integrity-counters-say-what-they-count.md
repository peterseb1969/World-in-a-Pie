---
section: fixes
cases: [1275]
packages: [wip-client]
---

### The integrity summary counts say what they count

`identity_hash_mismatches` and `identity_unverified` were described as document
counts while they count issue rows: `identity_unverified` gathers three checks
(a missing identity value, a missing resolution, a pinned template version that
cannot be found), so one document could account for several. Their descriptions
(in `@wip/client`'s `NamespaceIntegritySummary` as well) now say so, matching
`mandatory_fields_missing`, which already did. `documents_with_issues` remains the
count of distinct documents.
