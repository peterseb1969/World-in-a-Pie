---
section: fixes
cases: [1379]
---

### A namespace being deleted takes no more reporting writes, and a legacy prefix no longer blocks its deletion

- Once a namespace's deletion has closed it in reporting, a document,
  delete or template event no longer writes into its existing reporting
  tables. Before, only a new table was refused: an event for a table that
  already existed still wrote its rows. A write already under way when
  the namespace closes can still land, and the deletion's drop step
  removes it with the schema. A batch sync that reaches such a namespace
  fails, as it already did for a new table there.
- A namespace created before the prefix rules may have a prefix
  reporting cannot use as a schema name: `public`, `information_schema`,
  `pg_*`, one with a `"`, or one over 63 bytes. Deleting such a namespace
  used to fail at the reporting steps and stay locked, with
  `resume-delete` failing the same way. Both steps now answer 200. The
  drop clears the namespace's reporting bookkeeping and returns a
  `warning`, and the Registry records it on the step, so the deletion
  ends `completed_with_warnings`.
