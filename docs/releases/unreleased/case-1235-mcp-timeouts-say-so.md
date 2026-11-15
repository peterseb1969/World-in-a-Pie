---
section: fixes
cases: [1235]
---

### MCP: a timed-out call says it may still be running; an import waits for its rows

A call that outlived the MCP client's 30 s timeout answered `Error: ` with
nothing after it, so an agent read a CSV import that went on to complete as a
failure and could import it again. Every tool now answers a timeout as
"timed out waiting for WIP (<type>)", says the request may still be running,
and must be checked before it is retried. `import_documents_csv`,
`migrate_documents`, `delete_namespace` and `start_restore` say how to check
(for a restore whose archive upload timed out, before any job id came back,
through `list_backup_jobs`). An error with an empty message is named by its
type.

`import_documents_csv` now always previews the file and waits in proportion
to its row count (assumed 20 rows/s, plus a margin). A file it would wait for
longer than 300 s is refused before anything is written, naming the row
count one call takes, so split it. Making the import a job the agent polls is
tracked separately.
