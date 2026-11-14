---
section: fixes
cases: [1279]
---

### Registry claims and inserts say what they did

- **A failed batch insert that does not say where it stopped** is read back,
  and the claims are settled by what it wrote: the written entries' are
  confirmed, the rest released. `register` and `reserve` answer the written
  entries `created`/`reserved` instead of `error`. `provision` still fails as
  a whole, as before, with its claims settled the same way. Before, every
  claim stayed pending.
- **Activating a reservation under another key** gives back the claim on the
  key the reservation was made with. That key used to stay claimed by the
  entry, so nothing else could register it.
- **A confirmed claim carries no request token**, whichever path confirmed it,
  as the field's description says.
- **The pending reconcile** counts only the claims its filter matched. A claim
  taken over between its read and its write is not counted.
- **A claim taken over** is returned as stored.
- **The single-entry claim helpers** require the rollback token.
- **Register's timing** reports `4b_confirm_keys` again, apart from
  `4_insert_entries`.
- **The orphan audit** (`python -m registry.admin_backfill_claims`) is
  documented accurately. Its deletes no longer race requests, but it stays a
  deliberate step: it scans every claim, and the same entrypoint first strips
  duplicate synonyms (`backfill_claims`).

### Restores and namespace deletion

- **A system restore skips an archived reservation whose settings the API
  would refuse** (`invalid_record`, naming the setting), for example a
  `deletion_mode` other than `retain` or `full`. It used to store the
  reservation, which then failed on every later read.
- **`DELETE /api/registry/namespaces/{prefix}`** answers a refusal found
  while the delete starts with the refusal's own status. A namespace locked
  meanwhile is now 409 and one gone meanwhile is 404; before, both were 400.
  `wip`'s refusal no longer advises switching it to `full`, which the API
  forbids. The route's description names the dry run's `refusals` and
  `safe_to_delete`.
- **Deleting a namespace no longer clears another namespace's legacy id
  counters** when the other's name extends this one's with a `:`, for
  example `a` and `a:b`. Only names created before the prefix rule can hold
  a `:`.
