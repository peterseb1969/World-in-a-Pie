---
section: fixes
cases: [1283]
packages: [wip-auth, wip-archive]
---

### An ID-preserving restore refused by a reservation says what order avoids it

A platform admin's ID-preserving restore onto a name reserved here for
another namespace id is refused (409), as before. The refusal now names the
reservation's holder and says to restore a namespace's data before deploying
the apps or creating the keys that reserve its name, and how to restore now
(a runtime key's reservation: a platform admin releases it with `DELETE
/api/registry/reservations/{prefix}`). `docs/deployment/backup-restore.md`
states the order.

### Smaller fixes from a commit review

- A resolve whose answer was in flight when the entity's cache invalidation
  arrived no longer caches that answer afterwards (it was served for up to
  30 s).
- The field-form term lookup refuses a key several terms hold instead of
  taking it as a miss and trying its next reading of the terminology.
- An ambiguous Registry answer cut short at its 10 listed candidates no
  longer states that as the number of matches ("more than 10", "N or
  more"); it sets `details.truncated`, and the services' 409 carries
  `truncated`.
- The offline migratability check reports why a source version it carries
  cannot be resolved (an unpinned or circular parent, too deep a chain),
  not "version N is not in the archive".
- A namespace archive with no namespace config is restored with a job
  warning that it is not a re-creation of the archived namespace.
- Documentation: the `migrate_documents` per-document `identity_hash_stale`
  code (MCP tool, `@wip/client` README), the MCP `start_restore` modes, the
  guide's grants archived without a namespace id, and older release notes
  that still named the retired namespace import/export or said merges
  always carry term deprecations or that a retry overwrites orphaned bytes.
