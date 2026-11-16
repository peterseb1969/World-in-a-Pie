---
section: fixes
cases: [1407]
---

### document-store reads a Registry that answers 5xx as an outage, so an import stops on it

When the Registry answered a document-store call with an error status (500,
503: its own restart, overload, an unhandled error), the item was reported
as a refusal: `registry_error` without `details.rule`. Only a Registry that
could not be reached at all carried `details.rule: registry_unavailable`. An
import (`POST /api/document-store/import`), which stops at the first row that
meets an outage, therefore did not see one: during a Registry restart it
failed every remaining row as refused and completed.

A 5xx from the Registry is now an outage on every document-store Registry
call: minting document ids, synonym add and remove, identity re-key,
auto-synonym registration, and the namespace and deletion reads behind
storage deletes. The item carries `details.rule: registry_unavailable`, and
an import stops at that row, its job failed with the rows after it skipped.
A 4xx stays a refusal.

Auto-synonym registration after a create also reported an unreachable
Registry as a plain refusal. It now reports it as an outage too, on every
document it rolls back.

File uploads draw the same line. `POST /api/document-store/files` mints the file's id with its own Registry call, which still read a 5xx as a refusal: the upload answered 502 with no outage rule. It now answers 503, the same answer as for an unreachable Registry, and the error carries `details.rule: "registry_unavailable"` for callers that report per item. A 4xx stays a refusal (502).
