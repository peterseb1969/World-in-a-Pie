---
section: fixes
cases: [1282]
packages: [wip-archive]
---

### A number a document cannot store is refused wherever it sits

MongoDB holds a 64-bit integer at most. A larger whole number was refused
only in a scalar `integer` or `number` field. In an array item, a duration's
`value`, an untyped value or a document's `metadata` it passed validation,
the Registry allocated the document's id, and the insert then failed. Every
document written in the same insert (up to 1000 in a bulk create) was
reported as `internal_error` with it.

- **Document data:** such a number is now refused anywhere in `data` at the
  first validation stage, which already refused NaN and Infinity anywhere:
  `validation_failed`, per item, with code `maximum` or `minimum` and the
  value's path (`scores[1]`, `timeout.value`).
- **Metadata:** document `metadata` on create, `metadata_patch` on PATCH, and
  a file's `custom` metadata on upload and update refuse a whole number
  outside 64 bits, NaN and Infinity. The request is refused (422) naming each
  path, as for any other malformed request body.
- **File import:** a numeral cell longer than 4300 digits, mapped to an
  `integer` or `number` field, ended the whole import as a bare 500. It now
  fails its own row like any other cell its field cannot read.
- **wip-archive:** the offline validator (`wip verify`, `wip migrate`)
  applies the same first stage, so a document it passes is not refused on
  upload for such a number.
