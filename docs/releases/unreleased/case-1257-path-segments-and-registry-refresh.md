---
section: fixes
cases: [1257]
packages: [wip-client, wip-react]
---

### Two more request paths encode their values, and retiring writes refresh Registry listings

- **`@wip/client`:** `exportTerminology(id)` and `downloadBackupArchive(jobId)`
  were the two methods that still put their identifier into the request path
  unencoded. A terminology value with `#` or `?` could fetch another
  terminology's export. Both now encode it, like every other method. A test
  scans the service sources so a new method cannot interpolate a path value
  unencoded.
- **`@wip/react`:** the hooks that retire, reactivate or remove an entity now
  refresh the Registry listings and searches on screen (`registry.all`,
  active queries only), as the create hooks already did:
  - `useDeleteTerminology`, `useDeprecateTerm`, `useDeleteTerm`;
  - `useDeleteTemplate`, `useActivateTemplate`, `useReactivateTemplate`;
  - `useDeleteDocument`;
  - `useDeleteFile`, `useDeleteFiles`, `useHardDeleteFile`.

  A retired entity releases its synonyms, and a reactivated one claims them
  again, so a listing left alone showed them stale for up to five minutes.
