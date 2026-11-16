---
section: fixes
cases: [1282]
---

### A strict namespace refuses a reference the Registry cannot place

A document in a namespace with `isolation_mode: strict` may reference only
its own namespace and its allow-list. The check learns where a term or file
lives from the Registry, which answers only for an active entry. A term or
file whose entry was soft-deleted or removed at the Registry, or an id
several entries hold, came back with no namespace, and the check let the
reference through. Such a reference is now refused (`reference_violation`,
naming the id): a strict namespace admits only what it can show is inside
its bounds.

The Registry's bulk lookup can also answer one id with an error inside an
otherwise successful response. Every service that places references with it
(the strict check above, both stores' inbound checks, template-store's
reference check) read that id as "nowhere"; they now treat it as the
Registry being unavailable for that write. reporting-sync's
entity-references view, which hides references into namespaces the caller
cannot read, places entries of any status: it reads such an id on its own,
as it already read an id the lookup missed, and answers 503 only when that
read cannot place it either. One id the bulk lookup failed on no longer
turns the whole view into a 503.
