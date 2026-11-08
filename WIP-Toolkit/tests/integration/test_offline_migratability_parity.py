"""Offline migratability agrees with the live migrate dry run.

`inspect --target-version` (wip_archive.migratability) assesses a migration
on an archive alone. It must answer what `POST /documents/migrate` with
dry_run answers on the instance the archive was taken from: the same documents
ready, and the same (field, code) failures. The seed exercises each rule the
assessment follows — a declared rename, a newly mandatory field, a tightened
constraint, and a term deprecated after the document was written (which a
migration re-resolves and refuses, unlike verify).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from wip_archive.archive import ArchiveReader
from wip_archive.definitions import DefinitionIndex
from wip_archive.migratability import assess_migration

NS = "toolkit-migrate-it"

pytestmark = pytest.mark.usefixtures("stack")


def _created(result: dict, index: int = 0) -> dict:
    item = result["results"][index]
    assert item["status"] in ("created", "updated"), item
    return item


V1_FIELDS = [
    {"name": "code", "label": "Code", "type": "string", "mandatory": True},
    {"name": "colour", "label": "Colour", "type": "term", "terminology_ref": "COLOUR"},
    {"name": "name", "label": "Name", "type": "string"},
    {"name": "size", "label": "Size", "type": "integer"},
]
V2_FIELDS = [
    {"name": "code", "label": "Code", "type": "string", "mandatory": True},
    {"name": "colour", "label": "Colour", "type": "term", "terminology_ref": "COLOUR"},
    {"name": "title", "label": "Title", "type": "string", "mandatory": True},
    {"name": "size", "label": "Size", "type": "integer", "validation": {"maximum": 5}},
]
DOCS = [
    {"code": "A", "colour": "red", "name": "alpha"},              # ready: name renames to title
    {"code": "B", "colour": "blue"},                              # fails: title required
    {"code": "C", "colour": "grey", "name": "gamma"},             # fails: grey deprecated since
    {"code": "D", "colour": "red", "name": "delta", "size": 9},   # fails: size above the new maximum
    {"code": "E", "colour": "blue", "name": "eps", "size": 3},    # ready
]


def _seed(client) -> str:
    client.put("registry", f"/namespaces/{NS}", json={"description": "Offline migratability parity"})
    colour = _created(client.post(
        "def-store", "/terminologies",
        json=[{"value": "COLOUR", "label": "Colour", "namespace": NS}],
    ))
    terms = client.post(
        "def-store", f"/terminologies/{colour['id']}/terms",
        json=[{"value": v, "label": v.title()} for v in ("red", "blue", "grey")],
        params={"namespace": NS},
    )
    assert terms["succeeded"] == 3, terms
    grey = terms["results"][2]["id"]

    template = _created(client.post("template-store", "/templates", json=[{
        "value": "ITEM", "label": "Item", "namespace": NS,
        "identity_fields": ["code"], "fields": V1_FIELDS,
    }]))
    template_id = template["id"]
    for data in DOCS:
        _created(client.post("document-store", "/documents", json=[{
            "template_id": template_id, "template_version": 1, "namespace": NS, "data": data,
        }]))

    deprecated = client.post(
        "def-store", "/terms/deprecate",
        json=[{"term_id": grey, "reason": "retired"}], params={"namespace": NS},
    )
    assert deprecated["succeeded"] == 1, deprecated

    v2 = _created(client.post("template-store", "/templates", json=[{
        "value": "ITEM", "label": "Item", "namespace": NS,
        "identity_fields": ["code"], "fields": V2_FIELDS, "renames": {"title": "name"},
    }]))
    assert v2["version"] == 2, v2
    return template_id


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
        template_id = _seed(wip_client)
        live = wip_client.post(
            "document-store", "/documents/migrate",
            json={"template_id": template_id, "from_version": 1, "to_version": 2, "dry_run": True},
            params={"namespace": NS},
        )
        archive = tmp_path_factory.mktemp("migrate") / "engine-backup.zip"
        _engine_backup(stack, archive)
        _STATE.update(template_id=template_id, live=live, archive=archive)
    return _STATE


def _live_failures(live: dict) -> Counter:
    failures: Counter = Counter()
    for item in live["results"]:
        if item["status"] == "error":
            for error in (item.get("details") or {}).get("errors") or [
                {"field": None, "code": item.get("error_code")}
            ]:
                failures[(error.get("field"), error.get("code"))] += 1
    return failures


def test_offline_assessment_agrees_with_the_live_dry_run(corpus):
    live = corpus["live"]
    with ArchiveReader(str(corpus["archive"])) as reader:
        definitions = DefinitionIndex.load(reader)
        report = assess_migration(reader, definitions, corpus["template_id"], 2)

    assert report.blocked is None
    cohort = report.cohorts[1]
    assert cohort.documents == live["total"] == len(DOCS)
    assert cohort.ready == live["succeeded"]
    assert Counter(cohort.failures) == _live_failures(live)


def test_the_live_dry_run_answers_what_the_seed_pins(corpus):
    """The cases mean what they say: a guard against agreeing on nothing."""
    assert corpus["live"]["succeeded"] == 2
    assert _live_failures(corpus["live"]) == Counter({
        ("title", "required"): 1,
        ("colour", "invalid_term"): 1,
        ("size", "maximum"): 1,
    })


def test_inspect_target_version_reports_it(corpus):
    import json

    from click.testing import CliRunner
    from wip_toolkit.cli import main

    result = CliRunner().invoke(main, [
        "inspect", str(corpus["archive"]), "ITEM", "--target-version", "2", "--json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "1.3"
    cohort = payload["migratability"]["cohorts"][0]
    assert (cohort["from_version"], cohort["documents"], cohort["ready"]) == (1, 5, 2)
    assert payload["migratability"]["references_checked"] is False
