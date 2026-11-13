---
section: action-required
cases: [1268]
---

### A grant whose expiry has already passed is refused

`POST /api/registry/namespaces/{prefix}/grants` stored an `expires_at`
already in the past and answered `created`, though the grant gave nothing.
Such an item is now an error (`error_code` `validation_failed`,
`details.rule` `expired`), and an existing grant it names is left as it was.
`expires_at` is UTC; a value without an offset is read as UTC (the API
descriptions now say so). A grant's expiry is not announced to the other
services: the Registry stops honouring it at once, the others within 30 s.

**Action:** a script that re-grants with a lapsed expiry, expecting
`created`, must send a future expiry or none.
