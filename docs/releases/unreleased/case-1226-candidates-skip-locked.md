---
section: fixes
cases: [1226]
---

### A candidate lookup leaves out a namespace locked for deletion

`POST /api/registry/entries/lookup/candidates` with no `namespace` on an
item searched every namespace for an administrator or service key, so a
namespace being deleted still returned its entries as candidates and counted
them in `total`. It now searches every namespace except a locked one for
those keys (and, as before, the namespaces a key outside wip-admins and
wip-services can read), as every other listing that names no namespace
already does. The list is read once per request, and a namespace locked
while the request runs is checked again before its matches are counted.
