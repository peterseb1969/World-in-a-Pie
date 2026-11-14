---
section: fixes
cases: [1101]
packages: [wip-archive, wip-toolkit]
---

### Backups resolve every template pin, `wip export` keeps pinned versions, and an archive names the versions it left out

- **A backup resolves a template's `extends` as it resolves its nested
  pins**: a parent stored as its value or `NS:VALUE` (a row written before
  refs were resolved at write) keeps the version it extends. A malformed
  legacy template row (a non-string ref, a non-dict field, a dict where the
  field list belongs) is skipped instead of failing the whole backup, in the
  leave-out scan and in the archive's referent collector.
- **`wip export` keeps the template versions its documents pin.** Without
  `--include-inactive` it fetched active template versions only, while active
  documents can still be pinned to a superseded (inactive) version: such a
  document was exported without its schema, and `verify` refused the archive.
  Every version an exported document pins, and every version those extend or
  nest, is now carried. With `--skip-closure` the export does not read the
  documents first and says so.
- **An archive names the inactive template versions a backup left out.**
  `NamespaceEntry.template_versions_left_out` (`[{template_id, version}]`, in
  the manifest) tells "left out by design" from "lost"; it is absent when
  nothing was left out or with `include_inactive_templates`. The exclusion is
  now one query clause per template rather than one per left-out version.
