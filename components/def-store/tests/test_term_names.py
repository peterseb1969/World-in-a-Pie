"""Tests for the unified term-name model (names: typed, resolvable).

Covers: typed-name storage and projections, name-type validation against
_NAME_TYPES, the preferred-per-(type, language) rule, value precedence in
resolution, matched_name in validation responses, legacy-partition update
semantics, name search, and the startup migration of legacy
aliases/translations documents.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient

from def_store.models.term import Term, TermName
from def_store.services.term_names import (
    TermNameError,
    derived_aliases,
    derived_translations,
    migrate_legacy_term_names,
    normalize_names,
    replace_translations,
    replace_untyped,
    validate_names,
)


@pytest_asyncio.fixture
async def molecule_terminology(client: AsyncClient, auth_headers: dict):
    """A terminology for typed-name tests."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "MOLECULE",
            "label": "Molecule",
            "namespace": "wip",
            "case_sensitive": False,
        }]
    )
    return response.json()["results"][0]["id"]


# =============================================================================
# UNIT: normalization, validation, projections
# =============================================================================

def test_normalize_names_folds_legacy_shapes():
    from def_store.models.term import TermTranslation

    names = normalize_names(
        [TermName(value="bevacizumab", type="INN")],
        aliases=["Avastin"],
        translations=[TermTranslation(language="de", label="Bevacizumab-DE", description="d")],
    )
    assert [n.value for n in names] == ["bevacizumab", "Avastin", "Bevacizumab-DE"]
    assert names[1].type is None and names[1].language is None
    assert names[2].language == "de" and names[2].description == "d"


def test_validate_names_rejects_exact_duplicate_and_own_value():
    with pytest.raises(TermNameError, match="Duplicate name"):
        validate_names(
            [TermName(value="X"), TermName(value="X")], "V", {},
        )
    with pytest.raises(TermNameError, match="own value"):
        validate_names([TermName(value="V")], "V", {})
    # Case variants are legal — exact match only
    validate_names([TermName(value="x"), TermName(value="X")], "V", {})
    validate_names([TermName(value="v")], "V", {})


def test_validate_names_rejects_unknown_type_and_preferred_conflict():
    types = {"INN": "INN", "CODE": "Code"}
    with pytest.raises(TermNameError, match="Unknown name type"):
        validate_names([TermName(value="a", type="NOPE")], "V", types)
    with pytest.raises(TermNameError, match="preferred"):
        validate_names(
            [
                TermName(value="a", type="INN", preferred=True),
                TermName(value="b", type="INN", preferred=True),
            ],
            "V", types,
        )
    # One preferred per group is fine; different groups coexist
    validate_names(
        [
            TermName(value="a", type="INN", preferred=True),
            TermName(value="b", type="CODE", preferred=True),
            TermName(value="c", type="INN", language="de", preferred=True),
        ],
        "V", types,
    )


def test_projections_split_partitions():
    names = [
        TermName(value="bevacizumab", type="INN"),
        TermName(value="Avastin"),
        TermName(value="Bevacizumab-DE", language="de", description="d"),
        TermName(value="BEV", type="ABBREVIATION", language="de"),
    ]
    # aliases: every name's value (all names resolve)
    assert derived_aliases(names) == ["bevacizumab", "Avastin", "Bevacizumab-DE", "BEV"]
    # translations: language-bearing AND untyped only
    trs = derived_translations(names)
    assert [(t.language, t.label) for t in trs] == [("de", "Bevacizumab-DE")]


def test_replace_untyped_and_translations_touch_only_their_partition():
    names = [
        TermName(value="bevacizumab", type="INN"),
        TermName(value="Avastin"),
        TermName(value="Bevacizumab-DE", language="de"),
    ]
    replaced = replace_untyped(names, ["Altuzan"])
    assert {n.value for n in replaced} == {"bevacizumab", "Bevacizumab-DE", "Altuzan"}

    from def_store.models.term import TermTranslation
    replaced = replace_translations(names, [TermTranslation(language="fr", label="Bev-FR")])
    assert {n.value for n in replaced} == {"bevacizumab", "Avastin", "Bev-FR"}


# =============================================================================
# API: typed names end to end
# =============================================================================

@pytest.mark.asyncio
async def test_create_term_with_typed_names(client: AsyncClient, auth_headers: dict, molecule_terminology):
    response = await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{
            "value": "RO4876646",
            "label": "RO4876646",
            "names": [
                {"value": "bevacizumab", "type": "INN", "preferred": True},
                {"value": "Avastin", "type": "TRADE_GLOBAL"},
                {"value": "Altuzan", "type": "TRADE_LOCAL", "source": "TR"},
                {"value": "NSC-704865", "type": "REGISTRY_ID"},
                {"value": "RG435", "type": "CODE"},
            ],
            "aliases": ["rhuMAb-VEGF"],
        }]
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1, data["results"][0]
    term_id = data["results"][0]["id"]

    detail = (await client.get(
        f"/api/def-store/terms/{term_id}", headers=auth_headers
    )).json()
    by_value = {n["value"]: n for n in detail["names"]}
    assert by_value["bevacizumab"]["type"] == "INN"
    assert by_value["bevacizumab"]["preferred"] is True
    assert by_value["Altuzan"]["source"] == "TR"
    # The legacy alias arrived as an untyped name
    assert by_value["rhuMAb-VEGF"]["type"] is None
    # Derived flat projection covers every name
    assert set(detail["aliases"]) == {
        "bevacizumab", "Avastin", "Altuzan", "NSC-704865", "RG435", "rhuMAb-VEGF"
    }


@pytest.mark.asyncio
async def test_unknown_name_type_rejected(client: AsyncClient, auth_headers: dict, molecule_terminology):
    response = await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{
            "value": "X1",
            "names": [{"value": "x", "type": "NOT_A_TYPE"}],
        }]
    )
    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "invalid_names"
    assert "Unknown name type" in item["error"]


@pytest.mark.asyncio
async def test_preferred_conflict_rejected(client: AsyncClient, auth_headers: dict, molecule_terminology):
    response = await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{
            "value": "X2",
            "names": [
                {"value": "a", "type": "INN", "preferred": True},
                {"value": "b", "type": "INN", "preferred": True},
            ],
        }]
    )
    item = response.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "invalid_names"


@pytest.mark.asyncio
async def test_validate_returns_matched_name(client: AsyncClient, auth_headers: dict, molecule_terminology):
    await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{
            "value": "RO4876646",
            "names": [{"value": "bevacizumab", "type": "INN"}],
        }]
    )

    response = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={"terminology_id": molecule_terminology, "value": "bevacizumab"},
    )
    data = response.json()
    assert data["valid"] is True
    assert data["matched_via"] == "alias"
    assert data["matched_name"]["value"] == "bevacizumab"
    assert data["matched_name"]["type"] == "INN"
    assert data["matched_term"]["value"] == "RO4876646"


@pytest.mark.asyncio
async def test_validate_value_precedence_over_names(client: AsyncClient, auth_headers: dict, molecule_terminology):
    """A name on one term never shadows another term's canonical value."""
    # Term B carries a name equal to term A's value; create B FIRST so a
    # scan-order match would find B's name before A's value.
    await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{
            "value": "TERM_B",
            "names": [{"value": "TERM_A"}],
        }]
    )
    await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{"value": "TERM_A"}]
    )

    response = await client.post(
        "/api/def-store/validate",
        headers=auth_headers,
        json={"terminology_id": molecule_terminology, "value": "TERM_A"},
    )
    data = response.json()
    assert data["valid"] is True
    assert data["matched_via"] == "value"
    assert data["matched_term"]["value"] == "TERM_A"


@pytest.mark.asyncio
async def test_update_names_mutually_exclusive_with_legacy(client: AsyncClient, auth_headers: dict, molecule_terminology):
    create = await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{"value": "X3", "names": [{"value": "a"}]}]
    )
    term_id = create.json()["results"][0]["id"]

    response = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "names": [{"value": "b"}],
            "aliases": ["c"],
        }]
    )
    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["status"] == "error"
    assert "mutually exclusive" in item["error"]


@pytest.mark.asyncio
async def test_legacy_alias_update_preserves_typed_names(client: AsyncClient, auth_headers: dict, molecule_terminology):
    create = await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{
            "value": "X4",
            "names": [
                {"value": "typed", "type": "CODE"},
                {"value": "plain-old"},
                {"value": "Übersetzt", "language": "de"},
            ],
        }]
    )
    term_id = create.json()["results"][0]["id"]

    # Legacy aliases update: replaces only the untyped, language-less names
    response = await client.put(
        "/api/def-store/terms",
        headers=auth_headers,
        json=[{"term_id": term_id, "aliases": ["plain-new"]}]
    )
    assert response.json()["succeeded"] == 1

    detail = (await client.get(
        f"/api/def-store/terms/{term_id}", headers=auth_headers
    )).json()
    values = {n["value"] for n in detail["names"]}
    assert values == {"typed", "Übersetzt", "plain-new"}


@pytest.mark.asyncio
async def test_search_matches_typed_names(client: AsyncClient, auth_headers: dict, molecule_terminology):
    await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{
            "value": "RO4876646",
            "names": [{"value": "bevacizumab", "type": "INN"}],
        }]
    )

    response = await client.get(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        params={"search": "bevaciz"},
    )
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["value"] == "RO4876646"


# =============================================================================
# MIGRATION: legacy stored documents convert on startup
# =============================================================================

@pytest.mark.asyncio
async def test_migrate_legacy_term_names(client: AsyncClient, auth_headers: dict, molecule_terminology):
    # Create a term through the API, then rewrite it to the LEGACY stored
    # shape (aliases/translations fields, no names) via the raw collection
    create = await client.post(
        f"/api/def-store/terminologies/{molecule_terminology}/terms",
        headers=auth_headers,
        json=[{"value": "LEGACY1"}]
    )
    term_id = create.json()["results"][0]["id"]

    coll = Term.get_motor_collection()
    await coll.update_one(
        {"term_id": term_id},
        {
            "$set": {
                # Exact duplicate and own-value echoes must dedupe; case
                # variants must survive
                "aliases": ["L1", "L1", "l1", "LEGACY1"],
                "translations": [
                    {"language": "de", "label": "Legacy-DE", "description": "d"},
                    {"language": "fr", "label": "L1"},
                ],
            },
            "$unset": {"names": ""},
        },
    )

    result = await migrate_legacy_term_names()
    assert result["converted"] >= 1

    doc = await coll.find_one({"term_id": term_id})
    assert "aliases" not in doc and "translations" not in doc
    names = doc["names"]
    # L1 deduped (exact), l1 kept (case variant), own value dropped,
    # translations converted with language — and (L1, fr) is distinct
    # from the plain (L1, None) so it survives
    assert [(n["value"], n["language"]) for n in names] == [
        ("L1", None), ("l1", None), ("Legacy-DE", "de"), ("L1", "fr"),
    ]
    assert names[2]["description"] == "d"

    # Idempotent: a second run touches nothing
    rerun = await migrate_legacy_term_names()
    assert rerun["converted"] == 0
