---
section: first-start
---

def-store converts stored terms to the unified names shape before serving
traffic (legacy `aliases`/`translations` fields fold into `names`,
exact duplicates deduped first-occurrence-wins, the legacy alias index is
dropped). Idempotent and fast — look for `Term-name migration:` in the log.
Restoring a pre-upgrade archive is also covered: the restore engine
converts term rows at write time.

reporting-sync does this once, idempotently, while still connected as the
bootstrap login:

1. **Bookkeeping repair.** Copies of `_wip_schema_migrations` /
   `_wip_sync_status` that the old `"$user"` search path created in the `wip`
   schema are merged into `public` (the `wip` copy wins on conflict, being the
   newer one) and dropped. A copy with a different shape is renamed aside and
   logged as CRITICAL. Look for `Bookkeeping repair:` in the log.
2. **Service roles.** `wip_sync_owner` and `wip_reporting_read` are created
   (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`), every schema and relation
   owned by the bootstrap role is re-owned to `wip_sync_owner`, and the reader
   gets read grants plus default privileges. Look for
   `PostgreSQL roles ensured … re-owned N schemas, M relations`.
3. **Self-check.** Neither pool may run as a superuser or role creator; if one
   does, or a password is missing, `/health` answers 503 and `/status` reports
   `postgres_service_roles_ok: false`.

**Rollback caution:** an older reporting-sync image connects as `wip` again and
recreates empty unqualified bookkeeping tables in the `wip` schema. Prefer
redeploying forward; if you do roll back, reconcile with `/parity` and a batch
sync afterwards.
