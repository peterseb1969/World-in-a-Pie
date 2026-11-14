---
section: fixes
cases: [1294]
packages: [wip-archive, wip-toolkit]
---

### Archive tools count a row's version one way, and name a row whose version is not a number

- **`export-csv` no longer drops documents whose version is a string, missing,
  null or zero.** Its latest-only filter compared the row's raw version with
  the version the archive model had counted, so `"3"` never matched `3`, and
  a single-row document vanished from the CSV with nothing said.
- **A version that is not a number** (`"latest"`, `"1.0"`) in a document or
  template row, or in a pinned reference's `resolved.version`, stops every
  archive command with a message naming the row and exit 1. It used to end
  in a bare `ValueError` traceback, and `latest-only` hit it on a pinned
  reference it used to skip. `wip_archive` raises
  `wip_archive.exceptions.RowVersionError`, an `ArchiveError`;
  `wip_archive.model.row_version` is the one rule every reader uses, the
  template index included.
