---
section: fixes
cases: [845]
---

### Deleting an unknown or unreadable template answers `not_found`

`DELETE /api/template-store/templates` answered an item naming a template
that does not exist, or one in a namespace the caller cannot read, with
`error_code: invalid_request`, although its message said "not found". It now
answers `not_found`, as every other bulk door does for an entity the caller
cannot reach. The message is unchanged, so the two cases still read alike and
a namespace's contents are not confirmed to a caller who cannot read it.
