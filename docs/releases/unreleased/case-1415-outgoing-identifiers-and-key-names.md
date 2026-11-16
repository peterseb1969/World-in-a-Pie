---
section: fixes
cases: [1415]
---

### Three more outgoing identifiers are encoded, and a new API key may not name itself with '/'

Three calls still sent an identifier into another service's URL raw, so a
`#` or `?` in it cut the identifier short:

- **The MCP tools `get_api_key`, `update_api_key` and `revoke_api_key`.** A
  key named `ci#2` addressed the key `ci`. `revoke_api_key("ci#2")` revoked
  `ci` and reported success, while `ci#2` stayed enabled.
- **template-store's terminology lookup in def-store.** A field referencing
  the terminology `C#` was checked against the terminology `C`.
- **reporting-sync's parity sweep, re-reading templates by id.** Template ids
  are caller-assignable on restore and migration.

All three now send the identifier as one encoded path segment. The tests
that keep outgoing URLs encoded read each module's syntax tree instead of
single lines, so a URL split over two lines, or an id read from a dict, is
checked too. template-store gets the same guard.

**Action:** creating an API key whose name contains `/` is now refused
(422). The key routes take the name as one path segment, and `%2F` is
decoded back to `/` before routing, so such a key could never be read,
changed or revoked by name. Keys already stored, loaded from a key file or
restored under such a name are unaffected.
