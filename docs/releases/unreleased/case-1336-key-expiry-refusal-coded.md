---
section: fixes
cases: [1336]
---

### A key expiry already past is refused with a code

`POST /api/registry/api-keys` and `PATCH /api/registry/api-keys/{name}` refuse
an `expires_at` already in the past with 422, as before, and the body now
carries the code and rule the grants door gives the same refusal:
`"error_code": "validation_failed"`, `"details": {"rule": "expired", "field":
"expires_at"}`. `detail` is still the message string, so a client reading it is
unaffected; a client handling both grants and keys can branch on the code
instead of the text. The operator guide's PATCH example now says that
`expires_at: null` removes an expiry while null leaves any other field
unchanged.
