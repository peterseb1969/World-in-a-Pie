---
section: fixes
cases: [1097]
---

### A merge no longer zeroes file reference counts other namespaces' documents hold

After a merge, document-store recounted every file in the target namespace from
that namespace's documents alone, and from each document's latest version only.
A file referenced by a document in another namespace, or by a version
`older_versions: keep` left active, dropped to a count of 0, and a delete
without `force` then succeeded while documents still referenced it. A merged
document referencing a file stored in another namespace never raised that
file's count.

The recount now covers the files the merge can have changed (the namespace's
files and every file its documents reference, wherever it lives) and counts
each active document in any namespace that references the file, the platform's
own rule. The file's status follows the count (`active` above 0, `orphan` at 0;
an `inactive` file stays inactive).
