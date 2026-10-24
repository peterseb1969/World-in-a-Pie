"""reporting-sync's PostgreSQL roles: nothing it serves runs as a superuser.

The reporting database's bootstrap login (``POSTGRES_USER``, ``wip`` on a
standard install) is the superuser the PostgreSQL image creates. The service
used to do everything as that role, including executing caller-supplied SQL.
Now it runs as two ordinary roles:

- the **sync owner** owns every reporting schema and relation and does the
  DDL the sync pipeline needs (create/drop schemas, tables, indexes, views);
- the **reader** can only read what the owner owns, in read-only
  transactions with a statement timeout; caller-driven SQL runs as it.

Neither is a superuser, and neither can create roles or databases. The
bootstrap login is used once, at startup, on a single connection that is
closed before the service takes requests: it creates or normalises the two
roles, gives the owner what the bootstrap role owned, and grants the reader
read access. Every step is idempotent, so it runs on every start — which
also restores the roles after the database volume is rebuilt.
"""

import logging
from dataclasses import dataclass, field

import asyncpg

logger = logging.getLogger(__name__)

_ROLE_ATTRIBUTES = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
_SYSTEM_SCHEMA_FILTER = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg\\_%'"
)
# relkind -> the ALTER statement that changes its owner. Indexes and
# sequences owned by a column follow their table.
_ALTER_FOR_KIND = {
    "r": "TABLE",
    "p": "TABLE",
    "f": "FOREIGN TABLE",
    "v": "VIEW",
    "m": "MATERIALIZED VIEW",
    "S": "SEQUENCE",
}
# Serialises provisioning across concurrent starts against one database.
_PROVISION_LOCK_KEY = 0x5749505F524F4C45  # "WIP_ROLE"


@dataclass
class RoleSpec:
    owner: str
    owner_password: str
    reader: str
    reader_password: str
    reader_statement_timeout: str = "300s"


@dataclass
class ProvisionReport:
    roles_created: list[str] = field(default_factory=list)
    schemas_reowned: int = 0
    relations_reowned: int = 0


async def _ensure_role(conn: asyncpg.Connection, name: str, password: str) -> bool:
    """Create the role if missing and (re)set its attributes and password.

    Returns True when the role was created. The password is set on every
    start, so changing the secret and restarting rotates it. Utility
    statements take no bind parameters, so the statement is built with
    PostgreSQL's own quoting (format %I / %L) on the server.
    """
    created = not await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", name
    )
    if created:
        await conn.execute(
            await conn.fetchval(f"SELECT format('CREATE ROLE %I {_ROLE_ATTRIBUTES}', $1::text)", name)
        )
    await conn.execute(
        await conn.fetchval(
            f"SELECT format('ALTER ROLE %I WITH {_ROLE_ATTRIBUTES} PASSWORD %L', $1::text, $2::text)",
            name,
            password,
        )
    )
    return created


async def _quoted(conn: asyncpg.Connection, template: str, *args: str) -> str:
    """Build a utility statement with server-side identifier quoting."""
    placeholders = ", ".join(f"${i + 1}::text" for i in range(len(args)))
    return str(await conn.fetchval(f"SELECT format('{template}', {placeholders})", *args))


async def provision_roles(conn: asyncpg.Connection, spec: RoleSpec) -> ProvisionReport:
    """Create, normalise and grant the two service roles. Idempotent.

    ``conn`` must belong to a role that can create roles and change object
    ownership — the database's bootstrap superuser on a standard install.
    """
    report = ProvisionReport()
    database = await conn.fetchval("SELECT current_database()")
    bootstrap = await conn.fetchval("SELECT current_user")

    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _PROVISION_LOCK_KEY)
        for name, password in ((spec.owner, spec.owner_password), (spec.reader, spec.reader_password)):
            if await _ensure_role(conn, name, password):
                report.roles_created.append(name)

        # Pin search_path so an unqualified name can never resolve into a
        # schema named after the role (the bookkeeping split's cause).
        for name in (spec.owner, spec.reader):
            await conn.execute(await _quoted(conn, "ALTER ROLE %I SET search_path = public", name))
        await conn.execute(await _quoted(conn, "ALTER ROLE %I SET default_transaction_read_only = on", spec.reader))
        await conn.execute(await _quoted(
            conn, "ALTER ROLE %I SET statement_timeout = %L", spec.reader, spec.reader_statement_timeout,
        ))

        await conn.execute(await _quoted(conn, "GRANT CONNECT, CREATE ON DATABASE %I TO %I", database, spec.owner))
        await conn.execute(await _quoted(conn, "GRANT CONNECT ON DATABASE %I TO %I", database, spec.reader))
        await conn.execute(await _quoted(conn, "GRANT USAGE, CREATE ON SCHEMA public TO %I", spec.owner))
        await conn.execute(await _quoted(conn, "GRANT USAGE ON SCHEMA public TO %I", spec.reader))

    # Re-ownership: everything the bootstrap role owns in non-system
    # schemas moves to the owner — one transaction per schema, so a large
    # install stays under max_locks_per_transaction. Objects some other role
    # owns (an operator's own tables) are left alone.
    schemas = await conn.fetch(
        f"""
        SELECT n.nspname, pg_get_userbyid(n.nspowner) AS owner
        FROM pg_namespace n
        WHERE {_SYSTEM_SCHEMA_FILTER}
        ORDER BY n.nspname
        """  # nosec B608  # SQL fragments are code constants, values bound
    )
    for row in schemas:
        schema = row["nspname"]
        async with conn.transaction():
            if schema != "public" and row["owner"] == bootstrap:
                await conn.execute(await _quoted(conn, "ALTER SCHEMA %I OWNER TO %I", schema, spec.owner))
                report.schemas_reowned += 1
            relations = await conn.fetch(
                """
                SELECT c.relname, c.relkind::text AS relkind
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = $1
                  AND pg_get_userbyid(c.relowner) = $2
                  AND c.relkind::text = ANY($3::text[])
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend d
                      WHERE d.objid = c.oid AND d.deptype = 'a'
                        AND d.classid = 'pg_class'::regclass
                        AND d.refclassid = 'pg_class'::regclass
                  )
                """,
                schema,
                bootstrap,
                list(_ALTER_FOR_KIND),
            )
            for rel in relations:
                kind = _ALTER_FOR_KIND[rel["relkind"]]
                await conn.execute(await _quoted(
                    conn, f"ALTER {kind} %I.%I OWNER TO %I", schema, rel["relname"], spec.owner,
                ))
                report.relations_reowned += 1

    # The reader can read everything the owner owns, now and later.
    async with conn.transaction():
        owned = await conn.fetch(
            f"""
            SELECT n.nspname FROM pg_namespace n
            WHERE {_SYSTEM_SCHEMA_FILTER}
              AND (pg_get_userbyid(n.nspowner) = $1 OR n.nspname = 'public')
            """,  # nosec B608  # SQL fragments are code constants, values bound
            spec.owner,
        )
        for row in owned:
            await conn.execute(await _quoted(conn, "GRANT USAGE ON SCHEMA %I TO %I", row["nspname"], spec.reader))
            await conn.execute(await _quoted(
                conn, "GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I", row["nspname"], spec.reader,
            ))
        await conn.execute(await _quoted(
            conn, "ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT SELECT ON TABLES TO %I", spec.owner, spec.reader,
        ))
        await conn.execute(await _quoted(
            conn, "ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT USAGE ON SCHEMAS TO %I", spec.owner, spec.reader,
        ))

    logger.info(
        "PostgreSQL roles ensured (%s, %s): created %s; re-owned %d schemas, %d relations",
        spec.owner, spec.reader, report.roles_created or "none",
        report.schemas_reowned, report.relations_reowned,
    )
    return report


async def is_privileged(conn: asyncpg.Connection) -> bool:
    """True when the connection's role is a superuser or can create roles —
    a pool that must never serve requests."""
    return bool(await conn.fetchval(
        "SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user"
    ))
