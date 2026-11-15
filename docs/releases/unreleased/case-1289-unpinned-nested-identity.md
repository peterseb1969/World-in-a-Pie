---
section: action-required
cases: [1289]
packages: [wip-auth, wip-archive, wip-toolkit]
---

### An identity path into an unpinned nested template is refused, and an unclassifiable identity path is reported as such

- **template-store** refuses an identity path that runs into a nested template with no pinned version (`reference_violation`, rule `unpinned_nested_template`), where it used to record the path as naming no referent. Template versions stored before nested references had to pin a version are left unstamped at startup and reported; identity is never hashed from a submitted spelling.
- **document-store**'s validate-candidate agrees: a candidate with such a path fails with `identity_resolution_failed`.
- **wip-archive / wip-toolkit:** an identity path that cannot be classified is reported as such. Archive surgery refuses with a surgery error naming the path, instead of "identity would change (a fork)", and `inspect --target-version` records `identity_unclassifiable` for that path instead of counting the document as `identity_changed` / `identity_hash_stale`.
- **wip-auth** has a new public exception, `UnclassifiableIdentityPath` (`wip_auth.document_identity`), with a copy in wip-archive.

**Action:** a template whose identity field reaches into a nested template must pin that template's version (`template_ref_version`) before it is saved again; check the startup report for stored versions left unstamped.
