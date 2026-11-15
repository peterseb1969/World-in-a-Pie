---
section: fixes
cases: [1265]
---

### A Registry error on a retire or revive is an item error, not a 500

A Registry that answered a document's or template's retirement or revival
with an error status (a 4xx) made document-store and template-store answer
500, and a bulk document delete lost the results of the items it had
already deleted. Both now report it as `registry_error` and write nothing:
per item on the bulk doors, the rest of the batch standing. How each door
answers when the Registry does not record the change, correcting the earlier
note that said "on a single-object door, 503 for an outage and 502 for a
refusal" for every door:

- document-store's bulk doors: a per-item error, `details.rule:
  registry_unavailable` for an outage. After the first outage a bulk delete
  stops asking the Registry and refuses the remaining items the same way:
  each ask of a Registry that hangs cost the client's timeout twice.
- template-store's bulk doors: a per-item error for a refusal; an outage is
  a 503 for the whole request.
- `POST /templates/{id}/reactivate`: 503 for an outage, 502 for a refusal.
- Draft activation: 200 with an `activation_incomplete` error.
