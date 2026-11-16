---
section: operational
cases: [1295]
---

### Reporting: an index for "which documents hold this term", and a bound on the reference scans

- Every document reporting table now has a GIN index on `term_references_json` (`<table>_term_refs_idx`, `jsonb_path_ops`). It serves the containment lookup behind `/references/term/{id}/documents` and term `/referenced-by`.
- New tables get the index when they are created. An existing table gets it on its first sync event after the upgrade, once per table per process. Building the index briefly blocks writes to that table, once.
- The term and edge scans behind these routes are now bounded like search: 30 s to get a reader connection, and 30 s per query. Before, they could hold one of the five reader connections for the reader role's 300 s timeout.
- **Action:** none. To index an idle table ahead of its next event, run a batch sync of its template.
