---
section: action-required
cases: [1272]
packages: [wip-client]
---

### A Registry entry's `created_by` and `updated_by` are the authenticated caller; the caller's text is `actor_note`

Every write that changes a Registry entry used to copy `updated_by` from the
request item, a free-text field. A caller that sent none (the stores'
synonym-lifecycle reconcile pass, among others) stored `null` and erased the
record of who last changed the entry; a caller could also write any name.
Entries now follow the rule namespaces already follow: `updated_by` is the
identity the request authenticated as, and the text a caller sends as
`updated_by` is kept as the entry's new `actor_note`. Both are set on every
such write, so a write without text clears the note. This covers entry
update (`PUT /entries`), soft delete, `/entries/deactivate` and
`/entries/reactivate`, synonym add and remove, merge, and activation
(`/entries/activate`, keyed and keyless), which sends no text and so clears
the note. When one synonym-add request adds several synonyms to an entry,
the note is the text sent with the first addition that landed, never the
text of an item that was refused.

Creation follows the same rule. An entry's `created_by` (register, reserve,
provision) and a synonym's `created_by` (registered with the entry, added,
attached at activation, or moved by a merge) are the authenticated caller.
The `created_by` text a caller sends on register, reserve, provision or
synonym add becomes the entry's `actor_note`. On an activation synonym it is
accepted and not stored. A newly created entry's `updated_by` is `null`
until its first change (as a namespace's is): it names who last changed the
entry, and nobody has yet.

**Action:** a reader of `created_by` / `updated_by` on entries and synonyms
now sees `apikey:<name>` or the user's identity, not what the caller wrote.
Read `actor_note` for the caller's own words. The request fields keep their
names. Nothing stored is rewritten: an existing entry keeps its `created_by`,
and its `updated_by` until its next change. `@wip/client`'s entry types
carry `actor_note`.
