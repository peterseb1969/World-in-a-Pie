---
section: fixes
cases: [1282]
---

### A namespace's `id_config` read back and re-sent is accepted, and a null entry can be repaired

A namespace write that re-sends the `id_config` the namespace already holds
is accepted unchanged, even when the stored value predates the current
bounds. That held for a config re-sent as stored or as a read serves it,
except for an entry a read cannot serve as a config (stored before writes
were validated: not an object, or not a valid config). A read serves such an
entry as `{"invalid": "<reason>"}`, and re-sending it was refused (422), so an
app bootstrap that reads its namespace and writes the same body back stopped
there. That view now matches the entry it was served for, and the stored
value is kept.

A stored `null` entry was compared as if absent, that is as the platform
default. A write sending the default to repair it was taken for the stored
value: it answered 200 and stored nothing, and minting for that entity type
still failed. A null entry now matches only itself or its served view; any
config sent for it is validated and stored.
