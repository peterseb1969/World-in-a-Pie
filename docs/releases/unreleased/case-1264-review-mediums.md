---
section: fixes
cases: [1264]
---

### document-store: hidden things stay hidden, outages say so, a refused create gives back its id

- **A deleted file in a namespace the caller cannot read** answers `GET /files/{id}/content` as an id that names nothing (404). It answered 409 "File has been deleted", which told an outsider the file exists. A caller who can read the namespace still gets the 409.
- **An edge-type filter naming another namespace** (`template=` on `/documents/{id}/relationships`, `types=` on `/traverse`, as `NS:VALUE`) answers a namespace the caller cannot read with the same 404 as a value that names nothing. It is not resolved first: before, a template that existed there answered 200 and one that did not answered 404.
- **`registry_error` from an unreachable Registry carries `details.rule: registry_unavailable`** on every document path: minting a document's id, registering its inline synonyms (single and bulk create), and PATCH's write check and reference check. Some of these left the rule out, so the outage read like a refusal.
- **A create whose insert is refused** (a pinned version already taken, a concurrent writer) releases the Registry entry it had just minted, with its inline synonyms, as the create's other refusals do.
- **`POST /validation/validate-bulk`** reports an item whose validation met an outage (def-store, the template store or the Registry did not answer) as that item's result: `valid: false` with code `dependency_unavailable` or `registry_error`. It answered 503 for the whole batch and discarded every other item's result.
