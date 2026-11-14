---
section: fixes
cases: [1304]
---

### A restore no longer halts at the reporting check with "table missing" for every template

An ID-preserving restore wrote the Registry entries of everything it restored
last, after the documents. While the reporting check between templates and
documents ran, no restored template resolved. reporting-sync's batch sync got a
404 for each template's documents, created no table, and after 30 s the
restore halted with every template's table "missing". The namespace was left
partial, and later namespaces of the archive were never reached. A merge
wrote its entries after its documents the same way, so templates it added
with `add_missing` hit the same 404.

The Registry entries of terminologies, terms and templates are now written
with the definitions, before the reporting check, in both modes; documents'
and files' entries still come last. When the check does halt, its message now
names reporting-sync's own reason for each template whose sync failed, matched
to the template by its id (another namespace's template of the same value
lends it none), read within 10 s. A merge that halts there no longer tells the
operator to delete the namespace: it says the merge added definitions and no
documents, and to run it again once reporting-sync is fixed.

**Action:** a namespace a failed restore left partial is deleted and restored
again.
