"""Bookkeeping tables live in ``public``, and nowhere else.

reporting-sync keeps two bookkeeping tables, ``_wip_schema_migrations`` and
``_wip_sync_status``. They used to be created and read with unqualified
names, which resolve through ``search_path`` — ``"$user", public`` by
default. The service logs in as ``wip``, and the ``wip`` namespace's
reporting schema is also called ``wip``. Once that schema existed, an
unqualified ``CREATE TABLE IF NOT EXISTS`` created a second copy inside it,
and every later unqualified read and write went to that copy: the
bookkeeping silently split, with the live rows in the namespace schema and a
stale copy in ``public``.

Every access is now qualified with ``public.``. This module moves an
install's existing bookkeeping back where it belongs, once, at startup and
before anything else touches the tables. A schema named after the login role
is only a problem when it holds one of these tables; nothing else in it is
touched.
"""

import logging
from datetime import UTC, datetime

import asyncpg

logger = logging.getLogger(__name__)

# Table -> primary-key columns (the conflict target when merging rows).
BOOKKEEPING_TABLES: dict[str, tuple[str, ...]] = {
    "_wip_schema_migrations": ("namespace", "template_value", "template_version"),
    "_wip_sync_status": ("namespace", "template_value"),
}

# Serialises the repair across concurrent starts against one database.
_REPAIR_LOCK_KEY = 0x5749505F424B5250  # "WIP_BKRP"


async def _columns(conn: asyncpg.Connection, schema: str, table: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        ORDER BY ordinal_position
        """,
        schema,
        table,
    )
    return [r["column_name"] for r in rows]


async def _exists(conn: asyncpg.Connection, schema: str, table: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass(format('%I.%I', $1::text, $2::text)) IS NOT NULL",
        schema,
        table,
    ))


async def _rename_aside(
    conn: asyncpg.Connection, schema: str, table: str, new_name: str
) -> None:
    """Rename a table and its indexes out of the way.

    Index names are unique per schema, not per table: a table renamed aside
    keeps ``<table>_pkey`` and would block the current copy from moving into
    the same schema. Its indexes are renamed with the same suffix (trimmed
    to PostgreSQL's 63-byte identifier limit).
    """
    suffix = new_name[len(table):]
    indexes = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname = $1 AND tablename = $2",
        schema,
        table,
    )
    for row in indexes:
        renamed = (row["indexname"][: 63 - len(suffix)] + suffix)
        await conn.execute(
            f'ALTER INDEX "{schema}"."{row["indexname"]}" RENAME TO "{renamed}"'
        )
    await conn.execute(f'ALTER TABLE "{schema}"."{table}" RENAME TO "{new_name}"')


async def repair_bookkeeping_location(conn: asyncpg.Connection) -> list[str]:
    """Move bookkeeping rows held in the login role's schema into ``public``.

    Per table, when a copy exists in the schema named after the current
    login role:

    - no ``public`` copy: the table is moved into ``public``;
    - both copies namespace-keyed with the same columns: the role-schema
      rows are merged into ``public`` (they are the ones the service was
      reading and writing, so they win a conflict) and that copy is dropped;
    - ``public`` holds a pre-namespace-keying copy and the role schema a
      current one: the old ``public`` copy is renamed aside and the current
      one moved in;
    - any other mismatch: the role-schema copy is renamed aside and left for
      an operator. Both tables are rebuilt by a batch sync, so no data is
      lost that a sync cannot restore.

    Idempotent: with no role-schema copy it does nothing. Returns a log line
    per action taken.
    """
    actions: list[str] = []
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _REPAIR_LOCK_KEY)
        role_schema = await conn.fetchval("SELECT current_user")
        if role_schema == "public":
            return actions

        for table, key in BOOKKEEPING_TABLES.items():
            if not await _exists(conn, role_schema, table):
                continue
            shadow = f'"{role_schema}"."{table}"'
            stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

            if not await _exists(conn, "public", table):
                await conn.execute(f"ALTER TABLE {shadow} SET SCHEMA public")
                actions.append(f"moved {role_schema}.{table} to public")
                continue

            shadow_cols = await _columns(conn, role_schema, table)
            public_cols = await _columns(conn, "public", table)

            if shadow_cols == public_cols and "namespace" in shadow_cols:
                cols = ", ".join(f'"{c}"' for c in shadow_cols)
                conflict = ", ".join(f'"{c}"' for c in key)
                updates = ", ".join(
                    f'"{c}" = EXCLUDED."{c}"' for c in shadow_cols if c not in key
                )
                result = await conn.execute(
                    f"INSERT INTO public.\"{table}\" ({cols}) "
                    f"SELECT {cols} FROM {shadow} "
                    f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
                )
                await conn.execute(f"DROP TABLE {shadow}")
                actions.append(
                    f"merged {role_schema}.{table} into public ({result}) and dropped it"
                )
            elif "namespace" in shadow_cols and "namespace" not in public_cols:
                legacy = f"{table}__legacy_{stamp}"
                await _rename_aside(conn, "public", table, legacy)
                await conn.execute(f"ALTER TABLE {shadow} SET SCHEMA public")
                actions.append(
                    f"renamed pre-namespace public.{table} to public.{legacy} and "
                    f"moved {role_schema}.{table} to public"
                )
            else:
                aside = f"{table}__shadow_{stamp}"
                await _rename_aside(conn, role_schema, table, aside)
                actions.append(
                    f"left {role_schema}.{table} in place as {role_schema}.{aside}: "
                    f"its columns {shadow_cols} do not match public's {public_cols}"
                )

    for action in actions:
        if "do not match" in action:
            logger.critical("Bookkeeping repair: %s", action)
        else:
            logger.warning("Bookkeeping repair: %s", action)
    return actions
