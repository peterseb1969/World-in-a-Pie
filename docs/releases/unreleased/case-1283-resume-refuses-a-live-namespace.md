---
section: fixes
cases: [1283]
packages: [wip-deploy]
---

### Resuming a deletion never deletes a namespace that holds the name again

A deletion that completed with warnings does not hold its prefix, so the name
can be created again, and an ID-preserving restore gives the new namespace the
deleted one's namespace id and file ids. `POST
/api/registry/namespaces/{prefix}/resume-delete` then re-ran the degraded
steps, which delete by namespace. The resume could not tell the restored
namespace from the deleted one, because both have the same id, so it deleted
the restored file bytes and dropped the restored reporting schema. Such a
resume now answers **409**: what the old deletion left behind can no longer
be told apart from the live namespace's data, and deleting the namespace
removes both. A namespace under the name with another id is still told apart,
and its data is kept as before.

### document-store is never two processes during a Kubernetes update

On Kubernetes, document-store's Deployment used the default rolling update:
the new pod started while the old one still ran. The new pod's startup
recovery then failed the old one's running backups and restores, and freed the
one-restore-at-a-time claim while that restore still wrote. The Deployment now
uses the `Recreate` strategy, declared by the component (`single_process: true`
in its manifest).

### Smaller fixes

- A plain restore checks its file keys before it drops a stale reporting
  schema (`drop_stale_reporting`), so a restore refused for a held key leaves
  the schema in place.
- Terminology and template update and delete (`PUT`/`DELETE /terminologies`,
  `PUT`/`DELETE /templates`) resolve the entity they change through the
  Registry, not the 30-second read cache, as every other bulk write does.
- `wip://conventions` says where `ambiguous_reference` applies (a bulk item's
  identifier field, resolved in the item's namespace), that
  `details.total`/`details.truncated` say when the candidates are cut short,
  and that a term name in document data follows the term rule instead.
