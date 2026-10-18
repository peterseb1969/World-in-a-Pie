"""CASE-890 — registry's MongoDB role matches what namespace deletion touches.

The registry's MongoDB user may only `find` and `remove` in other services'
databases, on exactly the collections namespace deletion counts and deletes
in. That list lives twice: `_EXTERNAL_COLLECTIONS` in the code and the
`collection_privileges` of the registry manifest. A collection added to
one and not the other would fail at runtime with "not authorized" (or grant
access nobody uses), so this test keeps them equal.

The manifest is read with a regex rather than a YAML parser: the registry
does not depend on PyYAML.
"""

import re
from pathlib import Path

from registry.services.namespace_deletion import _EXTERNAL_COLLECTIONS

MANIFEST = Path(__file__).resolve().parents[1] / "wip-component.yaml"
_PRIVILEGE = re.compile(
    r"\{db: (?P<db>[\w-]+), collection: (?P<collection>[\w-]+), actions: \[(?P<actions>[^\]]*)\]\}"
)


def _manifest_privileges() -> dict[tuple[str, str], list[str]]:
    text = MANIFEST.read_text()
    return {
        (m["db"], m["collection"]): [a.strip() for a in m["actions"].split(",")]
        for m in _PRIVILEGE.finditer(text)
    }


def test_manifest_grants_exactly_the_collections_namespace_deletion_uses() -> None:
    granted = _manifest_privileges()
    used = {(db, coll) for db, coll, _field, _label in _EXTERNAL_COLLECTIONS}
    assert set(granted) == used


def test_cross_database_access_is_find_and_remove_only() -> None:
    for (db, coll), actions in _manifest_privileges().items():
        assert actions == ["find", "remove"], f"{db}.{coll}: {actions}"
