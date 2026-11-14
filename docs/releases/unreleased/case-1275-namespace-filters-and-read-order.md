---
section: fixes
cases: [1275]
packages: [wip-client]
---

### A `namespace` you filter on keeps working; a namespace is read-checked before use

`POST /documents/query` applied your readable-namespace scope with a plain merge,
so a filter on the `namespace` field was replaced rather than combined: asking
for `namespace = "acme"` answered with documents from every namespace the key can
read. Conditions on `namespace` now hold together — your filter and the
permission scope both apply, and naming a namespace you cannot read answers empty
instead of returning what you can. The same applies to the `namespace` parameter
of a listing beside a filter on the field.

`GET /table/{template_id}` and its `/csv` export resolved the template **in**
the `namespace` they were given before deciding whether the caller may read it.
Read is decided first now, so an unreadable namespace is refused there the way
`GET /documents` refuses it, before anything is looked up in it. A template the
caller cannot read also answers identically for a key scoped to several
namespaces: previously it said `… in namespace 'None'`, which distinguished
"exists, hidden" from an unknown id.

**Action:** none. Queries that filtered on `namespace` return fewer rows than
before — the rows they asked for.
