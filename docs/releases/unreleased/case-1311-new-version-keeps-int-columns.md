---
section: fixes
cases: [1311]
---

### A new template version no longer drops integer columns from the entity views

Adding a version to a template whose older per-version reporting tables predate the int8 mapping no longer drops the integer columns from the entity views: the new version table widens its int4 siblings to bigint as it is created, the same repair an existing table already got at its next sync. Namespace repair without a restore: run a batch sync for the template (`POST /api/reporting-sync/sync/batch/<VALUE>?namespace=<ns>`, `force` not needed) — it re-checks the catalog and widens every version table of the template.
