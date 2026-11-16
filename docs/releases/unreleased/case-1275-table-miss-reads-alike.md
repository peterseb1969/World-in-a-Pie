---
section: fixes
cases: [1275]
---

### The table routes no longer tell a hidden template from a missing one

`GET /api/document-store/table/{template_id}` and its `/csv` export answered
an unknown template id with "Template not found" and a template in a
namespace the caller cannot read with "Could not resolve template_id … in
namespace '…'", when the request named no namespace and the caller's key
implied one. The difference confirmed that the hidden template exists. Both
now answer the same 404 text.
