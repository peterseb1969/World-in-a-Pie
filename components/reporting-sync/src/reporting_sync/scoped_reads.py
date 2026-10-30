"""Caller SQL from non-privileged keys runs as a PostgreSQL role that can read
only the caller's namespaces.

The reporting reader role (``pg_roles.py``) can SELECT every namespace
schema, so checking which relations a caller's SQL names cannot contain it:
functions such as ``query_to_xml`` read whatever their string argument
says, and no dependency or plan analysis sees that. Postgres itself must
refuse the read. Each distinct set of readable namespaces gets a scope
role, and the caller's SQL runs on a connection that LOGS IN as that role.

The login role is the boundary. A session may switch to any role its login
role is a member of, and caller SQL can make that switch itself
(``set_config('role', …)``). A shared login that ``SET ROLE``\\s into a scope
is therefore escapable; a session whose login IS the scope role can only
switch to the namespace roles it already holds.

Roles:

- one **namespace role** per schema, ``NOLOGIN``, with USAGE and SELECT on
  that schema (granted by the sync owner, which owns it, including default
  privileges for relations created later);
- one **scope role** per readable set, ``LOGIN``, a member of exactly the
  namespace roles of its set. Its name is a hash of the set and its
  password is derived from the role manager's password, so every replica
  and every restart computes the same credentials without storing them.

Roles are created by the **role manager**, a dedicated login with
CREATEROLE that never serves requests. On PostgreSQL 16 such a role
administers only the roles it created, and is not a member of them.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass

import asyncpg

logger = logging.getLogger(__name__)

_SCOPE_PREFIX = "wip_scope_"
_NAMESPACE_PREFIX = "wip_nsread_"
_SCOPE_ATTRIBUTES = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
# Serialises role creation and grants across requests and replicas.
_SCOPE_LOCK_KEY = 0x5749505F53434F50  # "WIP_SCOP"


class ScopedReadsUnavailable(RuntimeError):
    """Scoped reads cannot be served: no role manager, or it cannot provision."""


def namespace_role(schema: str) -> str:
    return _NAMESPACE_PREFIX + hashlib.sha256(schema.encode()).hexdigest()[:32]


def scope_role(schemas: Iterable[str]) -> str:
    key = "\n".join(sorted(set(schemas)))
    return _SCOPE_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:32]


@dataclass
class ScopedReads:
    """Opens connections that can read only a given set of schemas.

    ``owner_pool`` belongs to the sync owner, which owns every namespace
    schema and grants read on it. ``endpoint`` is the host/port/database the
    scope roles log in to.
    """

    endpoint: dict[str, object]
    owner_pool: asyncpg.Pool
    manager_user: str
    manager_password: str
    statement_timeout: str = "300s"
    max_connections: int = 5

    def __post_init__(self) -> None:
        # Each scoped read opens its own connection; bound how many run at
        # once so callers cannot exhaust the server's connection slots.
        self._slots = asyncio.Semaphore(self.max_connections)

    def _password(self, role: str) -> str:
        return hmac.new(
            self.manager_password.encode(), role.encode(), hashlib.sha256
        ).hexdigest()

    async def _missing(self, scope: str, schemas: list[str]) -> tuple[bool, bool]:
        """(roles to provision, grants to apply) for this scope."""
        rows = await self.owner_pool.fetch(
            """
            SELECT s.schema,
                   EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = s.schema) AS schema_exists,
                   r.oid IS NOT NULL AS role_exists,
                   CASE WHEN r.oid IS NOT NULL AND sc.oid IS NOT NULL
                        THEN pg_has_role(sc.oid, r.oid, 'MEMBER') END AS member,
                   CASE WHEN r.oid IS NOT NULL
                         AND EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = s.schema)
                        THEN has_schema_privilege(r.oid, s.schema, 'USAGE') END AS granted
            FROM unnest($1::text[], $2::text[]) AS s(schema, role)
            LEFT JOIN pg_roles r ON r.rolname = s.role
            LEFT JOIN pg_roles sc ON sc.rolname = $3
            """,
            schemas, [namespace_role(s) for s in schemas], scope,
        )
        scope_exists = bool(await self.owner_pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", scope
        ))
        roles = not scope_exists or any(
            not r["role_exists"] or not r["member"] for r in rows
        )
        grants = any(r["schema_exists"] and not r["granted"] for r in rows) or roles
        return roles, grants

    async def _provision_roles(self, scope: str, schemas: list[str]) -> None:
        try:
            conn = await asyncpg.connect(
                **self.endpoint, user=self.manager_user, password=self.manager_password,
            )
        except (OSError, asyncpg.PostgresError) as e:
            raise ScopedReadsUnavailable(f"role manager cannot connect: {e}") from e
        try:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock($1)", _SCOPE_LOCK_KEY)
                for schema in schemas:
                    await _create_role(conn, namespace_role(schema), "NOLOGIN")
                if await _create_role(conn, scope, _SCOPE_ATTRIBUTES):
                    await _execute(conn, "ALTER ROLE %I PASSWORD %L", scope, self._password(scope))
                    await _execute(conn, "ALTER ROLE %I SET default_transaction_read_only = on", scope)
                    await _execute(conn, "ALTER ROLE %I SET search_path = public", scope)
                    await _execute(
                        conn, "ALTER ROLE %I SET statement_timeout = %L", scope, self.statement_timeout,
                    )
                    database = str(await conn.fetchval("SELECT current_database()"))
                    await _execute(conn, "GRANT CONNECT ON DATABASE %I TO %I", database, scope)
                for schema in schemas:
                    await _execute(conn, "GRANT %I TO %I", namespace_role(schema), scope)
        except asyncpg.PostgresError as e:
            raise ScopedReadsUnavailable(f"role manager cannot provision {scope}: {e}") from e
        finally:
            await conn.close()

    async def _apply_grants(self, schemas: list[str]) -> None:
        async with self.owner_pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _SCOPE_LOCK_KEY)
            existing = {
                r["nspname"] for r in await conn.fetch(
                    "SELECT nspname FROM pg_namespace WHERE nspname = ANY($1::text[])", schemas,
                )
            }
            for schema in schemas:
                if schema not in existing:
                    continue
                role = namespace_role(schema)
                # Default privileges first, so a relation created while the
                # grants run is covered by one or the other.
                await _execute(
                    conn, "ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO %I",
                    schema, role,
                )
                await _execute(conn, "GRANT USAGE ON SCHEMA %I TO %I", schema, role)
                await _execute(conn, "GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I", schema, role)

    async def _connect(self, scope: str) -> asyncpg.Connection:
        try:
            return await asyncpg.connect(
                **self.endpoint, user=scope, password=self._password(scope),
            )
        except asyncpg.InvalidPasswordError:
            # The role manager's password changed since the scope was
            # created: set the derived password again and retry once.
            conn = await asyncpg.connect(
                **self.endpoint, user=self.manager_user, password=self.manager_password,
            )
            try:
                await _execute(conn, "ALTER ROLE %I PASSWORD %L", scope, self._password(scope))
            finally:
                await conn.close()
            return await asyncpg.connect(
                **self.endpoint, user=scope, password=self._password(scope),
            )

    @contextlib.asynccontextmanager
    async def connection(self, schemas: Iterable[str]) -> AsyncIterator[asyncpg.Connection]:
        """A connection that can read exactly ``schemas`` (and nothing else).

        The connection is the caller's alone and is closed afterwards, so
        whatever session state the caller's SQL sets dies with it.
        """
        wanted = sorted(set(schemas))
        scope = scope_role(wanted)
        roles, grants = await self._missing(scope, wanted)
        if roles:
            await self._provision_roles(scope, wanted)
        if grants:
            await self._apply_grants(wanted)
        async with self._slots:
            conn = await self._connect(scope)
            try:
                yield conn
            finally:
                await conn.close()


async def _execute(conn: asyncpg.Connection, template: str, *args: str) -> None:
    """Run a utility statement built with server-side identifier quoting."""
    placeholders = ", ".join(f"${i + 1}::text" for i in range(len(args)))
    await conn.execute(str(await conn.fetchval(f"SELECT format('{template}', {placeholders})", *args)))


async def _create_role(conn: asyncpg.Connection, name: str, attributes: str) -> bool:
    if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", name):
        return False
    await _execute(conn, f"CREATE ROLE %I {attributes}", name)
    return True
