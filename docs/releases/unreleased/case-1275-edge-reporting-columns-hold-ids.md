---
section: fixes
cases: [1275]
---

### An edge's reporting columns hold the resolved endpoint id on both sync paths

`source_ref_id` and `target_ref_id` on an edge type's reporting table are the
canonical `document_id` of each endpoint. The event path already took them from
the payload's `source_ref_resolved` / `target_ref_resolved`; the batch path reads
documents without that enrichment and fell back to whatever the document stored.
An edge whose endpoint was written as a business key therefore failed its row on
a batch sync (`invalid input for query argument: expected str, got dict`) and
never reached PostgreSQL at all, and an endpoint written as any other synonym
landed as that spelling instead of the id, so the JOIN the design doc shows
returned nothing for it.

Batch sync now takes the same fact from the document's own `references` array, so
both paths write the canonical id. A row with neither — a legacy document with no
references entry — keeps its stored string ref, which still joins against a
document whose `document_id` equals it, and an endpoint that is neither resolved
nor a string leaves the column empty rather than claiming a non-id as an id.

**Action:** none required. Re-run a batch sync for an edge type whose rows went
missing (`POST /api/reporting-sync/sync/batch/<EDGE_TYPE>`) to fill them in.
