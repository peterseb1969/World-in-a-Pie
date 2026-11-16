---
section: fixes
cases: [1234]
packages: [wip-auth]
---

### Term validation accepts a terminology's canonical id in any id format again

`/api/def-store/validate` and `/validate/bulk` refused any terminology
identifier that was not UUID-shaped when no namespace came with it. A
namespace whose `id_config` mints prefixed or nanoid ids (`TERM-000001`)
has terminology ids of that shape. document-store validates term fields by
the terminology's canonical id, with no namespace, so every term field on
such a terminology failed a document write (`validation_failed`). A
canonical id now validates without a namespace, whatever its format. The
Registry confirms it is a terminology's id (an exact id match, no search by
value). A value or synonym still needs a namespace, as before.

Through the `ns:VALUE` form with no namespace, a terminology the caller
cannot read answered "Terminology not found", while one that does not exist
answered "Could not resolve terminology_id 'ns:VALUE' in namespace 'ns'".
That difference showed which values exist in a namespace the caller cannot
read. Both now give the second answer. Likewise, a canonical id the caller
cannot read is refused exactly as an id that names no terminology.
