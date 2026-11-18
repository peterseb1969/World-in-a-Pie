---
section: fixes
cases: [1430]
---

### An ambiguous reference into a namespace the writer may not reference no longer lists that namespace's entities

**What went wrong:** a reference that several entities answer is refused with `ambiguous_reference`, listing the candidates. The candidates were listed before the platform checked whether the writer may reference the namespace they live in. Take a namespace closed to inbound references (the default). A writer who cannot read it, referencing an `ns:VALUE` that several of its documents carry, was shown those documents: their ids, template values and matched keys. A value nothing carries got `reference_not_found`.

**Now:** each candidate passes the same inbound check as a resolved reference. If none is left, the write gets exactly what a value nothing carries gets. If some are left, only those are named. A writer who may reference the namespace (it reads it, or the namespace is open to inbound references) still sees the ambiguity with every candidate.

**Where:**
- document reference fields;
- `terminology_ref` on documents and templates, where def-store's answer to the service key, which sees every namespace, used to pass its candidates on unfiltered;
- a reference field's `target_templates`.

**Action:** none.
