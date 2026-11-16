"""The ``{source: target}`` map a redirected restore carries.

Rules over namespace names only — no archive, no database — so both callers of
a map can ask the same questions: the HTTP layer, which refuses a map the mode
cannot apply before it takes the request, and the restore engine, which refuses
the same shapes at plan time for whoever reaches it directly.

Stating each rule twice is what lets the two drift: the copies can key off
different values (the caller's raw parameters versus what the API derived from
them) and agree to disagree about one request. So the rules live here once, and
each layer only decides how a refusal reaches its caller — a 400 on the door, a
``RestoreEngineError`` in the engine.
"""

from __future__ import annotations

from collections import Counter


class NamespaceMapError(ValueError):
    """A ``{source: target}`` map the requested mode cannot apply."""


def validated_namespace_map(
    sources: list[str], namespace_map: dict[str, str]
) -> dict[str, str]:
    """Check a caller's {source: target} map against the archive's namespaces
    and return it in archive order.

    EVERY archived namespace must be mapped — restoring an unmapped namespace
    to its old name by default would silently collide with the live original,
    which is the accident both redirected modes exist to avoid. A namespace the
    map names that the archive does not carry is a typo with no effect, so it
    is refused rather than dropped. Archive order, not caller order:
    provisioning order and the per-pair staging both key off it.
    """
    unknown = sorted(set(namespace_map) - set(sources))
    unmapped = sorted(set(sources) - set(namespace_map))
    if unknown or unmapped:
        problems = []
        if unmapped:
            problems.append(f"unmapped archive namespace(s) {unmapped}")
        if unknown:
            problems.append(f"mapped but not in the archive: {unknown}")
        raise NamespaceMapError(
            "namespace_map must cover exactly the archive's "
            f"namespaces {sorted(sources)} — " + "; ".join(problems)
        )
    invalid = {s: t for s, t in namespace_map.items() if not t}
    if invalid:
        raise NamespaceMapError(
            f"namespace_map has empty target(s) for {sorted(invalid)}"
        )
    return {s: namespace_map[s] for s in sources}


def merge_map_targets(
    sources: list[str], target_namespace: str, namespace_map: dict[str, str]
) -> list[tuple[str, str]]:
    """The (source, target) pairs a redirected merge asks for, in archive order.

    The map is a merge's only statement of where its namespaces go:
    ``target_namespace`` names one destination for one namespace, so passing
    both leaves the request with two answers to the same question, and which
    one wins depends on which layer looks first.

    One destination per source. Several sources folding into one target is
    what a fresh restore does, and it pays for that with a collision pass
    over the folded set; a merge preserves ids and matches by content
    against one namespace at a time, so it refuses instead.
    """
    if target_namespace:
        raise NamespaceMapError(
            f"target_namespace '{target_namespace}' and namespace_map both "
            "name where the archive is written; pass one or the other."
        )
    mapping = validated_namespace_map(sources, namespace_map)
    shared = sorted(
        target for target, count in Counter(mapping.values()).items()
        if count > 1
    )
    if shared:
        raise NamespaceMapError(
            "namespace_map sends more than one archived namespace into the "
            f"same target: {shared}. A merge keeps every canonical id, so "
            "two sources folding into one namespace would need their ids "
            "checked against each other; merge them one at a time, or "
            "restore a copy with new ids (mode=fresh), which folds several "
            "sources into one namespace."
        )
    return [(source, mapping[source]) for source in sources]
