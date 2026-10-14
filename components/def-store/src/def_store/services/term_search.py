"""Ranked term search — the typeahead surface.

One call answers "type a name, get ranked candidates": exact, prefix,
word-start or substring matching over a term's value, label and names,
each hit carrying the exact string that matched, where it matched, and a
rank. Optionally rolls hits up to their root is_a ancestor, so a trade
name returns the molecule.

Search is deliberately case-insensitive regardless of the terminology's
case_sensitive flag: this is a discovery surface, not resolution — strict
matching (and value precedence) stays with /validate. Exact mode returns
ALL candidates rather than picking one, which makes it the ambiguity-
inspection surface: two terms sharing an alias both appear.

Backing: a namespace- (and optionally terminology-) bounded index scan
with an escaped case-insensitive regex, then match classification and
ranking in the service. Measured on the largest live vocabulary
(PCT_MOLECULE, 11,210 terms, 18,038 terms namespace-wide): 60-77 ms per
query — typeahead-viable without dedicated search indexes. Revisit with a
reporting-side trigram backing only if a measured latency problem appears
at ontology scale.
"""

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel

from ..models.api_models import TermResponse
from ..models.term import Term, TermName

logger = logging.getLogger(__name__)

SearchMode = Literal["exact", "prefix", "word-start", "substring"]

# Match-specificity tiers: a narrower match outranks a broader one even
# inside a broad mode (searching substring, an exact hit still ranks first)
_TIER_EXACT = 0
_TIER_PREFIX = 1
_TIER_WORD_START = 2
_TIER_SUBSTRING = 3

# Field tiers: the canonical value outranks the label outranks names;
# a preferred name outranks its siblings
_FIELD_VALUE = 0
_FIELD_LABEL = 1
_FIELD_PREFERRED_NAME = 2
_FIELD_NAME = 3

_WORD_BOUNDARY = re.compile(r"[^0-9A-Za-z]")


class TermSearchHit(BaseModel):
    """One ranked search hit."""

    term: TermResponse
    matched_value: str
    matched_via: Literal["value", "label", "alias"]
    matched_name: TermName | None = None
    rank: int
    # rollup=root only: the term that actually matched, when `term` was
    # replaced by its root ancestor
    matched_term: TermResponse | None = None


class TermSearchResponse(BaseModel):
    """Ranked search results."""

    query: str
    mode: str
    rollup: str
    items: list[TermSearchHit]
    total_matched: int


def _classify(candidate: str, query_lower: str) -> int | None:
    """Best specificity tier of `query` inside `candidate`, or None.

    Scans every occurrence: "xfu 5-FU" with query "fu" is a word-start
    match (the second occurrence), not merely a substring one.
    """
    c = candidate.lower()
    idx = c.find(query_lower)
    if idx < 0:
        return None
    if c == query_lower:
        return _TIER_EXACT
    best = _TIER_SUBSTRING
    while idx >= 0:
        if idx == 0:
            return _TIER_PREFIX
        if _WORD_BOUNDARY.match(c[idx - 1]):
            best = _TIER_WORD_START
        idx = c.find(query_lower, idx + 1)
    return best


_MODE_MAX_TIER = {
    "exact": _TIER_EXACT,
    "prefix": _TIER_PREFIX,
    "word-start": _TIER_WORD_START,
    "substring": _TIER_SUBSTRING,
}


class TermSearchService:
    """Ranked term search over value, label and names."""

    @staticmethod
    async def search(
        query: str,
        namespace: str,
        terminology_id: str | None = None,
        mode: SearchMode = "prefix",
        limit: int = 20,
        statuses: list[str] | None = None,
        rollup: Literal["none", "root"] = "none",
    ) -> TermSearchResponse:
        """Search terms by any of their names, ranked.

        Rank orders by match specificity (exact > prefix > word-start >
        substring), then matched field (value > label > preferred name >
        name), then the matched string alphabetically. The returned `rank`
        is the hit's position in that order (0-based).
        """
        from .terminology_service import TerminologyService

        statuses = statuses or ["active"]
        query_lower = query.lower()
        max_tier = _MODE_MAX_TIER[mode]

        # Bound the scan in Mongo with the loosest regex the mode allows;
        # exact classification happens in the service. The regex input is
        # LITERAL (escaped) — same rule as the list search.
        escaped = re.escape(query)
        if mode == "exact":
            pattern = f"^{escaped}$"
        elif mode == "prefix":
            pattern = f"^{escaped}"
        else:
            pattern = escaped
        regex = {"$regex": pattern, "$options": "i"}

        mongo_query: dict[str, Any] = {
            "namespace": namespace,
            "status": {"$in": statuses},
            "$or": [
                {"value": regex},
                {"label": regex},
                {"names.value": regex},
            ],
        }
        if terminology_id:
            mongo_query["terminology_id"] = terminology_id

        candidates = await Term.find(mongo_query).to_list()

        # Best match per term across its value, label and names
        scored: list[tuple[tuple[int, int, str], Term, str, str, TermName | None]] = []
        for term in candidates:
            best: tuple[int, int, str] | None = None
            best_fields: tuple[str, str, TermName | None] | None = None

            def consider(
                candidate: str | None,
                field_tier: int,
                via: str,
                name: TermName | None,
            ) -> None:
                nonlocal best, best_fields
                if not candidate:
                    return
                tier = _classify(candidate, query_lower)
                if tier is None or tier > max_tier:
                    return
                key = (tier, field_tier, candidate.lower())
                if best is None or key < best:
                    best = key
                    best_fields = (candidate, via, name)

            consider(term.value, _FIELD_VALUE, "value", None)
            if term.label and term.label != term.value:
                consider(term.label, _FIELD_LABEL, "label", None)
            for name in term.names:
                consider(
                    name.value,
                    _FIELD_PREFERRED_NAME if name.preferred else _FIELD_NAME,
                    "alias",
                    name,
                )

            if best is not None and best_fields is not None:
                scored.append((best, term, *best_fields))

        scored.sort(key=lambda row: row[0])
        total_matched = len(scored)
        scored = scored[:limit]

        items: list[TermSearchHit] = []
        for rank, (_, term, matched_value, via, matched_name) in enumerate(scored):
            items.append(TermSearchHit(
                term=TerminologyService._to_term_response(term),
                matched_value=matched_value,
                matched_via=via,  # type: ignore[arg-type]
                matched_name=matched_name,
                rank=rank,
            ))

        if rollup == "root":
            items = await TermSearchService._rollup_to_root(items, namespace)

        return TermSearchResponse(
            query=query,
            mode=mode,
            rollup=rollup,
            items=items,
            total_matched=total_matched,
        )

    @staticmethod
    async def _rollup_to_root(
        items: list[TermSearchHit], namespace: str
    ) -> list[TermSearchHit]:
        """Replace each hit's term with its root is_a ancestor, dedup by root.

        The root is reached by following the first is_a parent at each
        level (cycle-guarded, depth-capped) — with multiple parents the
        walk is deterministic but single-path; multi-parent semantics
        belong to the closure surface, not typeahead. A term with no
        parent is its own root. Hits sharing a root keep the best-ranked
        one, with the actual match preserved in matched_term.
        """
        from .ontology_service import OntologyService
        from .terminology_service import TerminologyService

        root_cache: dict[str, str] = {}

        async def root_of(term_id: str) -> str:
            if term_id in root_cache:
                return root_cache[term_id]
            seen = {term_id}
            current = term_id
            for _ in range(10):
                parents = await OntologyService.get_parents(current, namespace)
                if not parents:
                    break
                parent_id = parents[0].target_term_id
                if parent_id in seen:
                    break
                seen.add(parent_id)
                current = parent_id
            for tid in seen:
                root_cache[tid] = current
            return current

        rolled: dict[str, TermSearchHit] = {}
        for hit in items:
            root_id = await root_of(hit.term.term_id)
            if root_id in rolled:
                continue  # items arrive rank-ordered; first hit per root wins
            if root_id == hit.term.term_id:
                rolled[root_id] = hit
            else:
                root_term = await Term.find_one({
                    "namespace": namespace, "term_id": root_id,
                })
                if not root_term:
                    rolled[root_id] = hit
                    continue
                rolled[root_id] = TermSearchHit(
                    term=TerminologyService._to_term_response(root_term),
                    matched_value=hit.matched_value,
                    matched_via=hit.matched_via,
                    matched_name=hit.matched_name,
                    rank=hit.rank,
                    matched_term=hit.term,
                )

        return [
            hit.model_copy(update={"rank": i})
            for i, hit in enumerate(rolled.values())
        ]
