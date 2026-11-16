---
section: fixes
cases: [1295]
---

### Reporting: reference reads report canonical ids, find renamed tables, and authorize the term

- **`/entity/template/{id}/references` reports the canonical id.** A draft template's `terminology_ref` or `extends` may still hold a value. The reference's `ref_id` is now the id it resolved to, not the submitted value. Before, a value-form reference to a readable terminology was reported as broken.
- **Term and edge lookups find tables named by a `reporting.table_name` override.** They found only `doc_*` tables, so the documents of such a template were missing from `/references/term/{id}/documents` and `/referenced-by`.
- **`/references/term/{term_id}/documents` checks read on the term's namespace.** A caller who cannot read the term gets the answer for a term that does not exist: no documents.
- **HTML search snippets.** Document text that spells the snippet's internal markers no longer turns into `<b>…</b>`.
- **Speed.** Naming search hits and term documents reads the templates concurrently, at most eight at a time, instead of one after another.
