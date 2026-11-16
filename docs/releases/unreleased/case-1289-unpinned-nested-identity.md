---
section: action-required
cases: [1289]
packages: [wip-auth, wip-archive, wip-toolkit]
---

### An identity path into an unpinned nested template is refused, and an unclassifiable identity path is reported as such

- **template-store** refuses an identity path that runs into a nested template with no pinned version (`reference_violation`, rule `unpinned_nested_template`), where it used to record the path as naming no referent. At startup, every stored version with a nested identity path has its referent paths derived again: a version stamped before nested references had to pin a version is restamped, or left unstamped and logged when its path cannot be classified, so identity is never hashed from a submitted spelling.
- **document-store**'s validate-candidate agrees: a candidate with such a path fails with `identity_resolution_failed`.
- **wip-archive / wip-toolkit:** an identity path that cannot be classified is reported as such. Archive surgery refuses with a surgery error naming the path, instead of "identity would change (a fork)", and `inspect --target-version` records `identity_unclassifiable` for that path instead of counting the document as `identity_changed` / `identity_hash_stale`.
- **wip-auth** has a new public exception, `UnclassifiableIdentityPath` (`wip_auth.document_identity`), with a copy in wip-archive.

**Action:** look in template-store's startup log for `referent_identity_paths: <template> v<n> left unset`. Documents on such a version can no longer be written: every create or update fails with `identity_resolution_failed`. Save a new version of the template with the nested template's version pinned (`template_ref_version`), then run `migrate_documents` from the old version to the new one with `dry_run=true` first, which reports per document whether it can move. Any template whose identity field reaches into a nested template must pin that template's version before it is saved again.
