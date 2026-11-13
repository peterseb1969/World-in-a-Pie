---
section: fixes
cases: [1226]
---

### A candidate lookup leaves out a namespace locked for deletion

`POST /api/registry/entries/lookup/candidates` with no `namespace` on an
item searched every namespace for an administrator or service key, so a
namespace being deleted still returned its entries as candidates and counted
them in `total`. It now searches the namespaces the caller can read, which
for those keys is every namespace except a locked one, as every other
listing that names no namespace already does.
