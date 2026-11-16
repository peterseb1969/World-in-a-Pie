---
section: action-required
cases: [1326]
packages: [wip-client]
---

### A CSV/XLSX import runs as a job: `POST /import` answers 202 with it

`POST /api/document-store/import` wrote every row before it answered, at one
Registry call and one write per row (25–50 rows/s on a dev install, so about
eleven minutes for 30,000 rows), and a caller that stopped waiting did not stop
it. It now answers **202 with a job** of kind `import` (id `imp-…`) and writes
the rows in the background, as backup, restore and validation do. Poll
`GET /api/document-store/backup/jobs/{job_id}` (or its SSE stream at
`.../events`): the counts refresh on the record every 50 rows, and when
`status` is `complete` or `failed` its `result` is the import's report
(`result_kind: import_report`, the same counts and capped lists as before).
Import jobs are listed with the other jobs (`GET /backup/jobs`).

- What the caller can get wrong before any row is written is still refused by
  the request itself, and leaves no job: an unreadable file, a mapping naming
  columns the file has not got, an unusable template (422), an oversized
  upload (413).
- Rows that fail validation do not fail the job. A row that meets an outage
  does: the job is `failed`, its `error` names the row it stopped at, and its
  `result` holds the report so far, with the rows after it counted as skipped.
  Before, that was a 503 whose detail was the report.
- An import the process lost mid-run (a restart) is failed at startup with
  the counts it had recorded: the rows it wrote stand, and it cannot be
  resumed.
- `@wip/client`: `documents.importDocuments` returns the `BackupJobSnapshot`
  it started; `ImportDocumentsResponse` is gone, and the report is
  `ImportJobResult`, read from `job.result` (`BackupJobKind` gains `'import'`).
- MCP: `import_documents_csv` starts the job and watches it for up to four
  minutes, then answers with the job: terminal, with the report in `result`,
  or still running, in which case `get_backup_job` follows it. No file is
  refused for its length any more.

**Action:** a caller of `POST /import` reads the job instead of the report:
take `job_id` from the 202 and poll the job until `status` is terminal, then
read `result`. An app on `@wip/client` does the same with `importDocuments`
and `getBackupJob`, and replaces `ImportDocumentsResponse` with
`ImportJobResult`. There is no synchronous mode.
