"""The offline validator agrees with the live one.

`wip_archive.validation` reimplements document-store's validation so an archive
can be checked without a running platform (CASE-827 M2). A pre-upload gate that
disagrees with the server is worse than none, so this suite sends the same
documents through the live `POST /validation/validate` and through the offline
validator reading the real engine backup of the same namespace, and requires
the same verdict and the same (field, code) errors. Reference fields and file
existence are left out: the offline validator does not run those stages (a
file item's type is checked by both).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from wip_archive.archive import ArchiveReader
from wip_archive.definitions import DefinitionIndex
from wip_archive.validation import OfflineValidator

NS = "toolkit-validate-it"

pytestmark = pytest.mark.usefixtures("stack")


def _created(result: dict, index: int = 0) -> dict:
    item = result["results"][index]
    assert item["status"] in ("created", "updated"), item
    return item


def _seed(client) -> dict:
    ids: dict = {}
    client.put("registry", f"/namespaces/{NS}", json={"description": "Offline validation parity"})

    colour = _created(client.post(
        "def-store", "/terminologies",
        json=[{"value": "COLOUR", "label": "Colour", "namespace": NS}],
    ))
    ids["colour"] = colour["id"]
    terms = client.post(
        "def-store", f"/terminologies/{ids['colour']}/terms",
        json=[
            {"value": "red", "label": "Red", "aliases": ["crimson", "scarlet"]},
            {"value": "blue", "label": "Blue", "aliases": ["azure"]},
            {"value": "grey", "label": "Grey"},
        ],
        params={"namespace": NS},
    )
    assert terms["succeeded"] == 3, terms
    # "crimson" is borne by two terms: an ambiguous name. An ambiguous name
    # resolves to the first bearer in storage order. Offline, that order is
    # rebuilt from created_at (the archive has no insertion order), so two
    # bearers created in one request can tie and fall to a random id tail.
    # The second bearer is created in its own, later request.
    maroon = client.post(
        "def-store", f"/terminologies/{ids['colour']}/terms",
        json=[{"value": "maroon", "label": "Maroon", "aliases": ["crimson"]}],
        params={"namespace": NS},
    )
    assert maroon["succeeded"] == 1, maroon
    # A deprecated term no longer matches in new data.
    grey = terms["results"][2]["id"]
    deprecated = client.post(
        "def-store", "/terms/deprecate",
        json=[{"term_id": grey, "reason": "retired"}], params={"namespace": NS},
    )
    assert deprecated["succeeded"] == 1, deprecated

    address = _created(client.post("template-store", "/templates", json=[{
        "value": "ADDRESS", "label": "Address", "namespace": NS, "identity_fields": [],
        "fields": [
            {"name": "street", "label": "Street", "type": "string", "mandatory": True},
            {"name": "zip", "label": "Zip", "type": "string",
             "validation": {"pattern": "^[0-9]{5}$"}},
        ],
    }]))
    ids["address"] = address["id"]

    item = _created(client.post("template-store", "/templates", json=[{
        "value": "ITEM", "label": "Item", "namespace": NS,
        "identity_fields": ["code", "colour"],
        "fields": [
            {"name": "code", "label": "Code", "type": "string", "mandatory": True,
             "validation": {"pattern": "[A-Z]{3}", "min_length": 3, "max_length": 8}},
            {"name": "colour", "label": "Colour", "type": "term", "terminology_ref": "COLOUR",
             "mandatory": True},
            {"name": "tags", "label": "Tags", "type": "array", "array_item_type": "term",
             "array_terminology_ref": "COLOUR"},
            {"name": "count", "label": "Count", "type": "integer",
             "validation": {"minimum": 0, "maximum": 10}},
            {"name": "price", "label": "Price", "type": "number"},
            {"name": "active", "label": "Active", "type": "boolean"},
            {"name": "when", "label": "When", "type": "date"},
            {"name": "at", "label": "At", "type": "datetime"},
            {"name": "email", "label": "Email", "type": "string", "semantic_type": "email"},
            {"name": "site", "label": "Site", "type": "string", "semantic_type": "url"},
            {"name": "lat", "label": "Lat", "type": "number", "semantic_type": "latitude"},
            {"name": "share", "label": "Share", "type": "number", "semantic_type": "percentage"},
            {"name": "where", "label": "Where", "type": "object", "semantic_type": "geo_point"},
            {"name": "level", "label": "Level", "type": "string",
             "validation": {"enum": ["low", "high"]}},
            {"name": "status", "label": "Status", "type": "string"},
            {"name": "note", "label": "Note", "type": "string"},
            {"name": "flags", "label": "Flags", "type": "array", "array_item_type": "boolean"},
            {"name": "days", "label": "Days", "type": "array", "array_item_type": "date"},
            {"name": "stamps", "label": "Stamps", "type": "array", "array_item_type": "datetime"},
            {"name": "amounts", "label": "Amounts", "type": "array", "array_item_type": "number"},
            {"name": "files", "label": "Files", "type": "array", "array_item_type": "file",
             "array_file_config": {"allowed_types": ["*/*"]}},
            {"name": "home", "label": "Home", "type": "object",
             "template_ref": "ADDRESS", "template_ref_version": 1},
            {"name": "sites", "label": "Sites", "type": "array", "array_item_type": "object",
             "array_template_ref": "ADDRESS", "array_template_ref_version": 1},
        ],
        "rules": [
            {"type": "conditional_required", "target_field": "note",
             "conditions": [{"field": "status", "operator": "equals", "value": "closed"}]},
            {"type": "conditional_value", "target_field": "level", "allowed_values": ["high"],
             "conditions": [{"field": "count", "operator": "equals", "value": "10"}]},
            {"type": "mutual_exclusion", "target_fields": ["price", "share"]},
            {"type": "dependency", "target_field": "at",
             "conditions": [{"field": "when", "operator": "exists"}]},
        ],
    }]))
    ids["item"] = item["id"]

    # Identity is an array of terms: each element's resolved id feeds the hash.
    tagged = _created(client.post("template-store", "/templates", json=[{
        "value": "TAGGED", "label": "Tagged", "namespace": NS, "identity_fields": ["tags"],
        "fields": [
            {"name": "tags", "label": "Tags", "type": "array", "array_item_type": "term",
             "array_terminology_ref": "COLOUR", "mandatory": True},
        ],
    }]))
    ids["tagged"] = tagged["id"]
    return ids


BASE = {"code": "ABC", "colour": "red"}

CASES = {
    "valid minimal": {},
    "valid full": {
        "tags": ["blue", "azure"], "count": 3, "active": True, "when": "2026-10-31",
        "at": "2026-10-31T12:00:00Z", "email": "a@b.co", "site": "https://x.io", "lat": 45.5,
        "where": {"latitude": 1, "longitude": 2}, "level": "low", "status": "open",
        "home": {"street": "Main", "zip": "12345"}, "sites": [{"street": "A"}],
    },
    "term by alias": {"colour": "scarlet"},
    "term case-insensitively": {"colour": "RED"},
    "pattern must match the whole value": {"code": "ABCdef"},
    "datetime with +00:00": {"at": "2026-10-31T12:00:00+00:00", "when": "2026-10-31"},
    "boolean array items checked": {"flags": ["yes", 3, True]},
    "date array items checked": {"days": ["2026-10-31", "31.10.2026"]},
    "datetime array items checked": {"stamps": ["2026-10-31T12:00:00-05:00", "noon"]},
    "number array refuses booleans": {"amounts": [1, 2.5, True]},
    "file array item not a string": {"files": [42]},
    "number refuses a boolean": {"price": True},
    "datetime naive": {"at": "2026-10-31T12:00:00.123", "when": "2026-10-31"},
    "datetime with a bad offset": {"at": "2026-10-31T12:00:00+25:00", "when": "2026-10-31"},
    "missing mandatory": {"_drop": ["code"]},
    "null mandatory": {"code": None},
    "unknown field": {"nope": 1},
    "string too short": {"code": "AB"},
    "string too long": {"code": "ABCDEFGHIJ"},
    "pattern miss": {"code": "abc"},
    "enum miss": {"level": "medium"},
    "integer as string": {"count": "3"},
    "bool is not an integer": {"count": True},
    "integer below minimum": {"count": -1},
    "integer above maximum": {"count": 11},
    "number as string": {"price": "9.5"},
    "boolean as string": {"active": "true"},
    "bad date": {"when": "31.10.2026"},
    "datetime with +02:00": {"at": "2026-10-31T12:00:00+02:00", "when": "2026-10-31"},
    "invalid term": {"colour": "purple"},
    "deprecated term": {"colour": "grey"},
    "ambiguous name on an identity field": {"colour": "crimson"},
    "ambiguous name in an array (warning only)": {"tags": ["crimson"]},
    "invalid term in an array": {"tags": ["blue", "teal"]},
    "bad email": {"email": "not-an-email"},
    "bad url scheme": {"site": "ftp://x.io"},
    "latitude out of range": {"lat": 91},
    "percentage out of range": {"share": 101},
    "geo_point missing key": {"where": {"latitude": 1}},
    "semantic checked despite an earlier error": {"count": "x", "email": "bad"},
    "nested missing mandatory": {"home": {"zip": "12345"}},
    "nested pattern miss": {"home": {"street": "Main", "zip": "1234"}},
    "array object item not an object": {"sites": ["Main"]},
    "array object item missing field": {"sites": [{"zip": "12345"}]},
    "conditional_required": {"status": "closed"},
    "conditional_value with numeric coercion": {"count": 10, "level": "low"},
    "mutual_exclusion": {"price": 1.0, "share": 5},
    "dependency": {"at": "2026-10-31T12:00:00Z"},
}


def _data(case: dict) -> dict:
    data = {**BASE, **{k: v for k, v in case.items() if k != "_drop"}}
    for key in case.get("_drop", []):
        data.pop(key, None)
    return data


def _engine_backup(stack, archive_path: Path) -> None:
    from document_store.models.backup_job import BackupJob
    from document_store.services.backup_engine import DirectBackupEngine

    async def _run() -> None:
        mongo = BackupJob.get_motor_collection().database.client
        await DirectBackupEngine(mongo, None, lambda _e: None).run_backup(NS, archive_path)

    stack.portal.call(_run)


_STATE: dict = {}


@pytest.fixture()
def corpus(stack, wip_client, tmp_path_factory):
    if not _STATE:
        ids = _seed(wip_client)
        archive = tmp_path_factory.mktemp("validate") / "engine-backup.zip"
        _engine_backup(stack, archive)
        with ArchiveReader(str(archive)) as reader:
            definitions = DefinitionIndex.load(reader)
        _STATE.update(ids=ids, definitions=definitions)
    return _STATE


def _errors(errors: list[dict]) -> set[tuple]:
    return {(e.get("field"), e.get("code")) for e in errors}


@pytest.mark.parametrize("name", list(CASES))
def test_offline_agrees_with_live(corpus, wip_client, name):
    data = _data(CASES[name])
    live = wip_client.post(
        "document-store", "/validation/validate",
        json={"template_id": corpus["ids"]["item"], "namespace": NS, "data": data},
    )
    template = corpus["definitions"].resolve_template(corpus["ids"]["item"], 1).as_template_dict()
    offline = OfflineValidator(corpus["definitions"]).validate(template, data, NS)

    assert offline.valid == live["valid"], (name, live["errors"], offline.errors)
    assert _errors(offline.errors) == _errors(live["errors"]), name
    assert not offline.unverifiable, offline.unverifiable
    # Which bearer an ambiguous name records is creation order, which the
    # archive cannot reproduce for terms created in the same millisecond —
    # a declared divergence (wip_archive.validation); both sides warn.
    if live["valid"] and live.get("term_references") is not None and "ambiguous" not in name:
        assert {(r["field_path"], r["term_id"]) for r in offline.term_references} == {
            (r["field_path"], r["term_id"]) for r in live["term_references"]
        }, (name, offline.term_references, live["term_references"])
    # An ambiguous non-identity name warns on both sides.
    if "warning only" in name:
        assert any("ambiguous" in w for w in live["warnings"])
        assert any("ambiguous" in w for w in offline.warnings)


# What live answers for the cases that pin behaviour. A behaviour change
# changes a row here, on both validators at once.
EXPECTED = {
    "valid full": set(),
    "term by alias": set(),
    "pattern must match the whole value": {("code", "pattern")},
    "boolean array items checked": {("flags[0]", "invalid_type"), ("flags[1]", "invalid_type")},
    "date array items checked": {("days[1]", "invalid_type")},
    "datetime array items checked": {("stamps[1]", "invalid_type")},
    "number array refuses booleans": {("amounts[2]", "invalid_type")},
    "file array item not a string": {("files[0]", "invalid_type")},
    "number refuses a boolean": {("price", "invalid_type")},
    "datetime naive": set(),
    "datetime with a bad offset": {("at", "invalid_type")},
    "ambiguous name in an array (warning only)": set(),
    "missing mandatory": {("code", "required")},
    "unknown field": {("nope", "unknown_field")},
    "datetime with +02:00": set(),
    "deprecated term": {("colour", "invalid_term")},
    "ambiguous name on an identity field": {("colour", "ambiguous_term")},
    "semantic checked despite an earlier error": {
        ("count", "invalid_type"), ("email", "invalid_email"),
    },
    "nested missing mandatory": {("home.street", "required")},
    "array object item missing field": {("sites[0].street", "required")},
    "conditional_value with numeric coercion": {("level", "conditional_value")},
    "dependency": {("at", "dependency")},
}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_live_answers_what_the_cases_pin(corpus, wip_client, name):
    live = wip_client.post(
        "document-store", "/validation/validate",
        json={"template_id": corpus["ids"]["item"], "namespace": NS, "data": _data(CASES[name])},
    )
    assert _errors(live["errors"]) == EXPECTED[name], (name, live["errors"])


def test_invalid_cases_are_most_of_the_corpus(corpus, wip_client):
    """Guard against a parity suite that agrees because nothing fails."""
    invalid = sum(
        not wip_client.post(
            "document-store", "/validation/validate",
            json={"template_id": corpus["ids"]["item"], "namespace": NS, "data": _data(case)},
        )["valid"]
        for case in CASES.values()
    )
    assert invalid >= 30, invalid


TAGGED_CASES = {
    "unambiguous identity elements": ({"tags": ["blue", "azure"]}, set()),
    "ambiguous identity element": ({"tags": ["blue", "crimson"]}, {("tags[1]", "ambiguous_term")}),
}


@pytest.mark.parametrize("name", list(TAGGED_CASES))
def test_an_ambiguous_element_of_an_identity_array_is_refused(corpus, wip_client, name):
    """Each element of an array-of-terms identity field feeds its resolved id
    into the identity hash, so an ambiguous name there is refused on both
    sides rather than resolved by storage order."""
    data, expected = TAGGED_CASES[name]
    live = wip_client.post(
        "document-store", "/validation/validate",
        json={"template_id": corpus["ids"]["tagged"], "namespace": NS, "data": data},
    )
    template = corpus["definitions"].resolve_template(corpus["ids"]["tagged"], 1).as_template_dict()
    offline = OfflineValidator(corpus["definitions"]).validate(template, data, NS)

    assert _errors(live["errors"]) == expected, live["errors"]
    assert _errors(offline.errors) == expected, offline.errors
    assert offline.valid == live["valid"]
