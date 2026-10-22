"""Platform-maintained term closure — "the term or anything below it".

Every app that needs "documents whose term is anywhere in the subtree of
X" was re-deriving the same transitive closure with hand-rolled recursive
SQL or fixed-depth joins — and the measured data (926 multi-parent terms
in one real vocabulary) makes every fixed-depth join quietly wrong. The
closure is a materialization job, which is what reporting-sync is: a
`term_closure` table per namespace schema, one row per
(ancestor, descendant, relation_type) with the shortest-path depth, kept
in sync from the same term and term-relation events that feed the other
fixed tables.

Semantics:

- Rows are PER RELATION TYPE, never blended — an `is_a` subtree and a
  `part_of` subtree answer different questions. Every relation type with
  active edges in the namespace gets its closure.
- `is_a` edges come from BOTH explicit term relations and the terms
  table's `parent_term_id` (the same union def-store's hierarchy
  traversal uses — simple hierarchical terminologies carry no explicit
  relations).
- Reflexive rows (depth 0) are included for every active term, once per
  relation type present, so "X or anything below it" is one indexed
  `IN (SELECT descendant_id … WHERE ancestor_id = $1)` — no query-time
  recursion.
- Maintenance is a debounced FULL REBUILD per namespace, one
  DELETE + INSERT … WITH RECURSIVE inside a single transaction (readers
  never see a half-built closure). Incremental closure maintenance under
  deletes and multi-parent graphs is a genuinely hard problem; the
  rebuild is correct by construction and milliseconds at the measured
  scale (27K pairs). Revisit incremental only when a real ontology at
  SNOMED scale lands and the rebuild is MEASURED too slow.
- Cycles terminate through the depth cap (50, matching the ontology
  traversal cap); the final aggregation keeps the shortest path.
"""

import asyncio
import logging
import time

from .schema_manager import NamespaceDeletedError

logger = logging.getLogger(__name__)

# One lap more than def-store's ontology traversal allows, so anything
# reachable there is reachable here
MAX_DEPTH = 50

# Events arriving in a burst (a seed script's thousands of edge writes)
# coalesce into one rebuild
DEBOUNCE_SECONDS = 3.0


class TermClosureMaintainer:
    """Rebuilds a namespace's term_closure table, debounced."""

    def __init__(self, pool, schema_manager):
        self.pool = pool
        self.schema_manager = schema_manager
        self._dirty: set[str] = set()
        self._pending: dict[str, asyncio.Task] = {}

    def mark_dirty(self, namespace: str) -> None:
        """Schedule a debounced rebuild for the namespace.

        Safe to call per event: a pending rebuild absorbs further marks,
        and a mark landing mid-rebuild triggers one more pass.
        """
        self._dirty.add(namespace)
        task = self._pending.get(namespace)
        if task is None or task.done():
            self._pending[namespace] = asyncio.create_task(
                self._debounced(namespace)
            )

    async def _debounced(self, namespace: str) -> None:
        while True:
            await asyncio.sleep(DEBOUNCE_SECONDS)
            self._dirty.discard(namespace)
            try:
                await self.rebuild(namespace)
            except NamespaceDeletedError:
                # Deleted between the term event and this debounced rebuild:
                # there is nothing left to rebuild.
                logger.info("term_closure rebuild skipped: namespace %s was deleted", namespace)
                return
            except Exception as e:
                logger.error(
                    "term_closure rebuild failed for %s: %s", namespace, e
                )
            if namespace not in self._dirty:
                return

    async def close(self) -> None:
        """Cancel pending rebuilds (shutdown)."""
        for task in self._pending.values():
            if not task.done():
                task.cancel()
        self._pending.clear()

    async def rebuild(self, namespace: str) -> int:
        """Rebuild the namespace's closure in one transaction.

        Returns the number of closure rows written.
        """
        start = time.perf_counter()
        closure = await self.schema_manager.ensure_term_closure_table(namespace)
        relations = await self.schema_manager.ensure_term_relations_table(namespace)
        terms = await self.schema_manager.ensure_terms_table(namespace)

        sql = f"""
WITH RECURSIVE edges AS (
    SELECT "source_term_id" AS child, "target_term_id" AS parent,
           "relation_type"
    FROM {relations}
    WHERE "namespace" = $1 AND "status" = 'active'
    UNION
    SELECT "term_id", "parent_term_id", 'is_a'
    FROM {terms}
    WHERE "namespace" = $1 AND "status" = 'active'
      AND "parent_term_id" IS NOT NULL
),
walk AS (
    SELECT child AS descendant, parent AS ancestor, relation_type,
           1 AS depth
    FROM edges
    UNION ALL
    SELECT w.descendant, e.parent, w.relation_type, w.depth + 1
    FROM walk w
    JOIN edges e
      ON e.child = w.ancestor AND e.relation_type = w.relation_type
    WHERE w.depth < {MAX_DEPTH}
),
all_pairs AS (
    SELECT descendant, ancestor, relation_type, depth FROM walk
    UNION ALL
    SELECT t."term_id", t."term_id", rt.relation_type, 0
    FROM {terms} t
    CROSS JOIN (SELECT DISTINCT relation_type FROM edges) rt
    WHERE t."namespace" = $1 AND t."status" = 'active'
)
INSERT INTO {closure} (
    "namespace", "relation_type", "ancestor_id", "descendant_id",
    "depth", "ancestor_terminology_id", "descendant_terminology_id"
)
SELECT $1, p.relation_type, p.ancestor, p.descendant, MIN(p.depth),
       ta."terminology_id", td."terminology_id"
FROM all_pairs p
LEFT JOIN {terms} ta
       ON ta."namespace" = $1 AND ta."term_id" = p.ancestor
LEFT JOIN {terms} td
       ON td."namespace" = $1 AND td."term_id" = p.descendant
GROUP BY p.relation_type, p.ancestor, p.descendant,
         ta."terminology_id", td."terminology_id"
"""

        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f'DELETE FROM {closure} WHERE "namespace" = $1', namespace
            )
            result = await conn.execute(sql, namespace)

        # asyncpg returns 'INSERT 0 <n>'
        rows = int(result.split()[-1]) if result else 0
        logger.info(
            "term_closure rebuilt for %s: %d rows in %.1fms",
            namespace, rows, (time.perf_counter() - start) * 1000,
        )
        return rows
