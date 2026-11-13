---
section: fixes
cases: [1263]
---

### A restore whose completion cannot be recorded still syncs and verifies what it wrote

When a restore finished but its job record could not take the final state
(MongoDB unavailable through every retry), the restore's data was committed
but its namespaces were never synced to PostgreSQL and never validated. The
follow-ups are now started anyway, and the job's error names which of them
started and how to run any that could not. A restore the process was still
running when it ended (a pod restart, an OOM kill) gets the same at the next
startup, where it was only marked failed before. The write that marks
such a job failed is retried as well, and covers a job still `pending` (its
first write was lost too), so no job is left looking alive with no run
behind it. A backup's failure message no longer claims follow-ups it never
owed.

### Restore from a retained job: an expired archive is a 410, and a failed job creation leaves no archive copy

- In local mode (no file storage), restoring from a job whose archive was past
  the retention window answered a bare 500: the scratch sweep removed the
  archive just after it had been reported as retained. The sweep now runs
  first, and the answer is the 410 "Archive no longer retained". An archive
  that disappears after the check anyway, locally or from the bucket, is the
  same 410.
- With file storage, a job record that failed to save left the new job's
  archive copy in the bucket with nothing pointing at it. The copy is now
  removed on any failure before the record exists, and a cleanup that cannot
  reach the store no longer replaces the original error with its own.

### A backup taken with `skip_documents` leaves out the documents' Registry entries

The archive carried a Registry entry for each document it left out. Restored,
those entries claimed the documents' identity keys with no document behind
them, so a later create with the same identity was deduped onto an entry that
resolves to nothing. The entries are now left out with their documents.
Archives already written this way still carry them. A plain restore's dry run
now counts what the real run writes, without skipped types and without the
edges `drop_unresolved_edges` leaves out.
