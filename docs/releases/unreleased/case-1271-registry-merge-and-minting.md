---
section: fixes
cases: [1271]
---

### Registry: merges into a retired entry are refused; a released key moves back; validation-only id algorithms answer per item

- **A merge into an entry retired meanwhile changes nothing.** The survivor
  is read again, active only, when the merge writes it. A survivor retired or
  deleted since the merge read it refuses the merge (`concurrency_conflict`),
  and the deprecated entry keeps its keys; the merge reported `merged` and left
  the deprecated identity on an entry nothing resolves to.
- **A key the survivor released resolves to it after a merge.** When the
  survivor held only a released (inactive) copy of a key the deprecated entry
  held actively, the merge moved the key's claim to the survivor but left the
  survivor's copy inactive: the key was claimed and resolved to nothing. The
  survivor's copy is now replaced by the active one.
- **A namespace whose id algorithm only validates answers per item.** The
  `pattern` and `any` algorithms check IDs a caller supplies and mint none. A
  register item without an `entry_id` in such a namespace answered the whole
  batch with HTTP 500; it is now that item's error, and provision answers 422.
- **Namespace deletion counts inbound documents in the database.** The dry run
  and the delete grouped every referencing document version in memory; they
  now aggregate per (namespace, template). A count summed over more than one
  batch of 50 000 ids is reported as `up to N document version(s)`, since a
  version holding ids from two batches counts in each.
