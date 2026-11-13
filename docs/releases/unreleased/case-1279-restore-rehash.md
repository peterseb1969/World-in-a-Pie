---
section: fixes
cases: [1279]
---

### A restore rehashes documents against the template they were written with

A restore that re-mints ids recomputes each document's identity hash
whenever its rewritten references feed that hash. Several documents kept the
archived hash, so the next write of the same identity created a second
document:

- **Inherited identity.** An archived template that extends another stores
  neither its inherited fields nor, unless it declares its own, its identity
  fields. The rehash read the stored row and saw no identity. It now
  composes the template along its pinned parents, as template-store does.
- **Templates the archive does not carry** were read from this install at
  their latest version. Now every version the install holds is read, and
  each document uses the version it is pinned to. A merge into several
  targets reads each version once.
- **A pinned version the archive does not carry** left the document
  unrehashed. Identity fields cannot change between versions, so another
  version of the same template now stands in.
- **A row whose identity values or resolutions cannot be read** kept its hash
  silently. It is now counted with the other unrehashable documents. The
  restore's warning names both causes: a template (or a parent it extends)
  held nowhere, and an unreadable row.

### A restore's credentials, as documented

- An API key a restore passes on to the Registry is sent under the install's
  configured API-key header, the one it arrived on. It used to be sent under
  `X-API-Key` whatever the configuration.
- The release notes and the namespace-upsert docstring now say what the
  Registry does:
  - A login holding `admin` on a reserved prefix can create it by restore.
  - Only an API key can own a runtime reservation.
  - Creating or configuring a namespace uses the requester's credential.
  - The restore's other Registry calls still use the service key.
