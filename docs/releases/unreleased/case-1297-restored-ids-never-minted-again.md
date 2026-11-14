---
section: fixes
cases: [1297]
---

### A restored prefixed ID is never minted again

An ID-preserving restore or a merge writes the Registry entries an archive
carries straight to the database, so the Registry never saw their IDs. A
Registry process reads a prefix's highest existing ID once, the first time it
mints from it. A process that had minted from a prefix before a restore
therefore minted the restored IDs again, and those writes failed with
`already_exists` until the Registry restarted.

Restores and merges now send each batch of entry IDs to the new privileged
route `POST /api/registry/entries/advance-counters` before writing it. The
route raises every prefixed sequence's counter past the IDs it numbers. A
Registry that does not answer stops the restore before that batch is written.

Also fixed in the Registry:

- The highest existing ID of a prefix is read by number: IDs minted under
  different pad widths no longer leave a shorter, higher one uncounted.
- An ID whose number is beyond the range a counter mints (above 2^62) no
  longer moves a counter. A single oversized ID used to stop minting for its
  prefix across the install.
- When an ordered batch insert fails partway, only the item it failed on gets
  that error. The items after it answer `internal_error` with
  `details.rule: batch_stopped` and the failing index: they were not
  attempted, and their own IDs may be free.
