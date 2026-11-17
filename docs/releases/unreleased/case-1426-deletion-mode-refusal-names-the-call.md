---
section: fixes
cases: [1426]
---

### A deletion_mode refusal names a call that works as written

Deleting a namespace whose `deletion_mode` is `retain` used to answer "Set deletion_mode='full' first via PATCH." without saying how to call PATCH. The two routes that set the mode take it in different places: `PUT /api/registry/namespaces/{prefix}` in the JSON body, `PATCH /api/registry/namespaces/{prefix}` as query parameters. A caller who sent PUT's body shape to PATCH therefore got FastAPI's bare 422. The refusal now names both calls in full. PATCH's own refusal for a missing `confirm_enable_deletion` now says the flag is a query parameter. Neither route changed what it accepts.
