---
section: action-required
cases: [1302]
packages: [wip-client]
---

### A runtime API key's expiry already past is refused, and an update can remove an expiry

`POST /api/registry/api-keys` stored an `expires_at` already in the past
and returned the plaintext of a key that never authenticated; `PATCH
/api/registry/api-keys/{name}` with a past date expired a working key at
once. Both now answer 422 and change nothing. `expires_at` is UTC; a value
without an offset is read as UTC (the descriptions now say so).

An update can now remove a key's expiry: `expires_at` sent as `null` makes
the key never expire; left out, the expiry is unchanged. This applies to
`expires_at` only: the other fields stay unchanged when sent as `null` (a
`namespaces: null` would mean unrestricted). `@wip/client`'s
`UpdateAPIKeyRequest.expires_at` accepts `null`.

**Action:** a script that creates or renews a key with a lapsed date must
send a future one; a client that sends `expires_at: null` on update meaning
"unchanged" must leave the field out instead.
