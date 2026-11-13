---
section: fixes
cases: [1263]
packages: [wip-client, wip-auth]
---

### A document's bare identity synonym is refused at every door, for inherited identity too

The Registry refuses a document synonym whose keys are exactly its template's
identity fields (rule `bare_identity_synonym`): such a synonym claims the
values for every template in the namespace, and every later backup of the
namespace would refuse to restore. Three gaps are closed:

- **Inherited identity.** A template that extends another and declares no
  identity of its own stores none; the rule read only the stored list and
  let such documents' bare synonyms through. It now uses the effective
  identity (own, else the nearest parent's), as template-store does. The
  restore's archive check applies the same rule, and reads a template the
  archive does not carry from this install's template-store, as the
  Registry does, so a restore refuses up front what the Registry would
  refuse halfway through.
- **`POST /synonyms/merge`** moved the merged entry's synonyms onto the
  preferred entry unjudged; a synonym ordinary on one document could become
  the other's bare identity. Each moving key is now judged against the
  preferred entry's template, and the merge item is refused on a hit.
- **An unreadable template-store** made `/synonyms/add` and
  `/entries/register` fail the whole request with a 500. Each document
  synonym the rule would judge is now refused per item (`internal_error`,
  rule `bare_identity_synonym`); other items go through.

### A namespace locked for deletion refuses a privileged writer's references into it

A platform administrator or service key passed the inbound reference gate
without asking the Registry, so it could write a reference (a template's
`extends` or `terminology_ref`, a document's term or file) into a namespace
being deleted; the reference then outlived the namespace. Every writer now
gets the Registry's answer, which is read everywhere for these keys except a
namespace locked for deletion.

### Template version events say when they cannot judge a migration, and a prefill change is not a modification

- A version that inherits from another parent or parent version (`extends`
  / `extends_version`) was offered as an additive migration, although the
  new parent could require fields the diff does not list (they are
  inherited, not declared); every document then failed the migration. The
  diff now carries `parent_changed`, and `migration.eligible` is `null`
  with a pointer to the dry run.
- A changed `default_value` alone was reported as a modified field
  (`modified_existing`, migration not eligible), and kept two otherwise
  identical template versions apart when a merge aligned templates. It is
  now listed under `default_value_changed`, blocks nothing, and does not
  affect alignment. The platform never applies a default.
- `@wip/client`'s `TemplateVersionEventDetails` carries both new keys.

### A template has one reporting sync_strategy

`reporting.sync_strategy` could differ between versions of one template
(it is changed in place, on one version), and reporting-sync then deleted
rows an `all_versions` version's table must keep. A write that sets it, in
place or on a new version, now sets it for every version of the template.
As before for an in-place change, an existing reporting table keeps the
shape it was built with until the template is batch-synced with `force`.
