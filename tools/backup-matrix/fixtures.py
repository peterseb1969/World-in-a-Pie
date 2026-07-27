"""Backup/restore test-matrix fixtures — the §3 seed, built through public APIs.

Two small namespaces (NS-A rich, NS-B counterpart) plus the ambient ``wip``
namespace, spanning the E1-E15 entity checklist from
``docs/design/backup-restore-test-matrix.md`` §3. Every entity is created
through the public REST surface only (no Mongo hand-writes), idempotently, so
any layer can rebuild the fixture and a re-run converges rather than piling up
versions.

The concrete domain is a downsized mirror of the real ontology namespace Peter
seeded on kb: a controlled vocabulary with aliases, a real OBO ontology imported
as term-relations (E2, folding in the CASE-658 import refresher), specimen /
sample entities linked across the two namespaces, an edge type, an append-only
log, files, and the namespace-config variants that drive the N:1-collapse and
isolation cells.

After building, :meth:`FixtureBuilder.count` reads every entity class back and
returns an EXPECTED_COUNTS table — the conservation baseline the archive/restore
cells (X-02) reuse. Counts are MEASURED, not hardcoded, so the ontology import's
real term/relation totals become the expectation automatically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wip_http import ApiError, WipClient, check_bulk

# The wip-namespace terminology an NS-A document references (E8: a term ref that
# points OUT of the archived namespaces into the ambient ``wip`` shared vocab).
# Platform-provided and present on every instance; the runner never writes wip,
# so it must reference — never create — this.
WIP_TERM_TERMINOLOGY = "_TIME_UNITS"
WIP_TERM_VALUE = "hours"

# A terminology value deliberately shared between NS-A and NS-B. When two
# sources collapse into one target (R-FRC / N:1), a same-valued terminology in
# both is the collision the restore must refuse at plan time.
SHARED_TERMINOLOGY_VALUE = "MATRIX_STATUS"


@dataclass
class BuildReport:
    """What a build produced — the human-facing provisioning summary."""

    ns_a: str
    ns_b: str
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_storage_enabled: bool = False

    def step(self, msg: str) -> None:
        self.steps.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


class FixtureBuilder:
    """Provisions and counts the NS-A / NS-B fixture against one deployment."""

    def __init__(
        self,
        client: WipClient,
        *,
        ns_a: str,
        ns_b: str,
        ontology_file: Path,
        created_by: str = "backup-matrix-fixtures",
    ):
        self.c = client
        self.ns_a = ns_a
        self.ns_b = ns_b
        self.ontology_file = ontology_file
        self.created_by = created_by
        self.report = BuildReport(ns_a=ns_a, ns_b=ns_b)

    # ------------------------------------------------------------------ build

    def build(self) -> BuildReport:
        """Provision the whole fixture, in dependency order."""
        self._namespaces()
        self._terminologies_ns_b()  # NS-B terms first: NS-A shares a value
        self._terminologies_ns_a()
        self._import_ontology()  # E2 — real OBO content into NS-A
        self._template_sample()  # NS-B SAMPLE first: NS-A SPECIMEN pins it
        self._template_edge_type()  # NS-B LINKED_TO edge type (E4)
        self._template_specimen()  # NS-A SPECIMEN (E3, E8, E13, E14)
        self._template_event_log()  # NS-A EVENT_LOG (E7 append-only)
        self._documents_samples()  # NS-B samples, no back-ref yet
        self._documents_specimens()  # NS-A specimens ref NS-B samples (E6, E8)
        self._document_backref()  # PATCH NS-B SAMP-1 -> NS-A SPEC-1 (each-way)
        self._document_edge()  # NS-B LINKED_TO edge doc (E5)
        self._documents_event_log()  # NS-A append-only docs (E7)
        self._files()  # E9 — best-effort, loud if storage disabled
        self._custom_synonym()  # E10 — a synonym beyond the auto ones
        self._inactivate()  # E12 — retire one of each type
        return self.report

    # --- namespaces (E15) --------------------------------------------------

    def _namespaces(self) -> None:
        # NS-B: open isolation, but must reference NS-A (back-ref) and wip.
        # Prefixed sequential id_config on documents (E11). The registry's
        # entry_id unique index is GLOBAL while the prefixed counter is
        # per-namespace, so a fixed prefix would collide across the fresh
        # namespaces the runner mints each run — derive it from the namespace
        # name to keep it globally unique per run.
        doc_prefix = f"{self.ns_b}-D"
        self.c.put(
            f"/api/registry/namespaces/{self.ns_b}",
            json_body={
                "description": "Backup-matrix fixture NS-B (counterpart)",
                "isolation_mode": "open",
                "allowed_external_refs": [self.ns_a],
                "deletion_mode": "full",
                "id_config": {
                    "documents": {
                        "algorithm": "prefixed",
                        "prefix": doc_prefix,
                        "pad": 6,
                    }
                },
                "updated_by": self.created_by,
            },
        )
        self.report.step(
            f"namespace {self.ns_b}: open, id_config {doc_prefix} (E11), deletion_mode full"
        )

        # NS-A: STRICT isolation with an explicit allow-list (E15 strict
        # variant). Strict means wip is NOT automatic, so wip is listed too —
        # which is exactly what lets E8's wip term ref resolve.
        self.c.put(
            f"/api/registry/namespaces/{self.ns_a}",
            json_body={
                "description": "Backup-matrix fixture NS-A (rich)",
                "isolation_mode": "strict",
                "allowed_external_refs": ["wip", self.ns_b],
                "deletion_mode": "full",
                "updated_by": self.created_by,
            },
        )
        self.report.step(
            f"namespace {self.ns_a}: strict + allow-list [wip, {self.ns_b}] (E15), deletion_mode full"
        )

    # --- terminologies + terms + aliases (E1) ------------------------------

    def _terminologies_ns_b(self) -> None:
        # Shared-valued terminology (N:1 collision driver) + a distinct one
        # (N:1 success driver).
        self._create_terminology(self.ns_b, SHARED_TERMINOLOGY_VALUE, "Matrix Status (NS-B)")
        self._create_terms(
            self.ns_b,
            SHARED_TERMINOLOGY_VALUE,
            [
                {"value": "OPEN", "label": "Open"},
                {"value": "CLOSED", "label": "Closed"},
            ],
        )
        self._create_terminology(self.ns_b, "MATRIX_PRIORITY", "Matrix Priority")
        self._create_terms(
            self.ns_b,
            "MATRIX_PRIORITY",
            [
                {"value": "HIGH", "label": "High"},
                {"value": "LOW", "label": "Low"},
            ],
        )
        self.report.step(
            f"NS-B terminologies: {SHARED_TERMINOLOGY_VALUE} (N:1 collision), MATRIX_PRIORITY (N:1 success)"
        )

    def _terminologies_ns_a(self) -> None:
        # E1: terminology + terms + ALIASES, and a term to retire later (E12).
        self._create_terminology(self.ns_a, "MATRIX_COLOR", "Matrix Color")
        self._create_terms(
            self.ns_a,
            "MATRIX_COLOR",
            [
                {"value": "RED", "label": "Red", "aliases": ["r", "crimson"]},
                {"value": "GREEN", "label": "Green", "aliases": ["g"]},
                {"value": "BLUE", "label": "Blue", "aliases": ["b", "navy"]},
                {"value": "GREY", "label": "Grey", "aliases": ["gray"]},
            ],
        )
        # Same VALUE as NS-B's, different namespace: the N:1-collapse pair.
        self._create_terminology(self.ns_a, SHARED_TERMINOLOGY_VALUE, "Matrix Status (NS-A)")
        self._create_terms(
            self.ns_a,
            SHARED_TERMINOLOGY_VALUE,
            [
                {"value": "DRAFT", "label": "Draft"},
                {"value": "FINAL", "label": "Final"},
            ],
        )
        self.report.step("NS-A terminologies: MATRIX_COLOR (+aliases, E1), MATRIX_STATUS (shared value)")

    # --- ontology import (E2) ---------------------------------------------

    def _import_ontology(self) -> None:
        if not self.ontology_file.is_file():
            raise RuntimeError(f"Ontology file not found: {self.ontology_file}")
        data = json.loads(self.ontology_file.read_text())
        result = self.c.post(
            "/api/def-store/import-export/import-ontology",
            params={
                "namespace": self.ns_a,
                "terminology_value": "MATRIX_ONTOLOGY",
                "terminology_label": "Matrix Ontology (imported)",
                "created_by": self.created_by,
            },
            json_body=data,
        )
        terms = result.get("terms", {})
        rels = result.get("relations", {})
        self.report.step(
            f"NS-A ontology import (E2): {terms.get('total')} terms, "
            f"{rels.get('total')} relations from {self.ontology_file.name}"
        )

    # --- templates ---------------------------------------------------------

    def _template_sample(self) -> None:
        # NS-B entity template. An unconstrained back-ref field to keep the
        # cross-namespace document cycle acyclic at template level (only NS-A's
        # SPECIMEN carries the constrained cross-source pin).
        self._upsert_template(
            {
                "value": "MATRIX_SAMPLE",
                "label": "Matrix Sample",
                "namespace": self.ns_b,
                "identity_fields": ["sample_code"],
                "header_fields": ["sample_code", "label"],
                "reporting": {"sync_enabled": True},
                "fields": [
                    _f("sample_code", "string", mandatory=True),
                    _f("label", "string"),
                    {
                        "name": "origin_specimen",
                        "label": "Origin Specimen",
                        "type": "reference",
                        "reference_type": "document",
                        "mandatory": False,
                    },
                ],
            }
        )
        self.report.step("NS-B template MATRIX_SAMPLE (target of NS-A pin; back-ref field)")

    def _template_edge_type(self) -> None:
        # E4: edge type, versioned:false. Endpoints are same-namespace SAMPLEs.
        # Idempotent: skip if it already exists (usage/versioned are immutable).
        if self._template_exists(self.ns_b, "MATRIX_LINKED_TO"):
            self.report.step("NS-B edge type MATRIX_LINKED_TO already present (skip)")
            return
        self._upsert_template(
            {
                "value": "MATRIX_LINKED_TO",
                "label": "Matrix Linked-To",
                "namespace": self.ns_b,
                "usage": "relationship",
                "versioned": False,
                "identity_fields": ["source_ref", "target_ref"],
                "source_templates": ["MATRIX_SAMPLE"],
                "target_templates": ["MATRIX_SAMPLE"],
                "reporting": {"sync_enabled": True},
                "fields": [
                    {
                        "name": "source_ref",
                        "label": "Source",
                        "type": "reference",
                        "reference_type": "document",
                        "target_templates": ["MATRIX_SAMPLE"],  # must match source_templates
                        "mandatory": True,
                    },
                    {
                        "name": "target_ref",
                        "label": "Target",
                        "type": "reference",
                        "reference_type": "document",
                        "target_templates": ["MATRIX_SAMPLE"],  # must match target_templates
                        "mandatory": True,
                    },
                ],
            }
        )
        self.report.step("NS-B edge type MATRIX_LINKED_TO (E4: usage=relationship, versioned=false)")

    def _template_specimen(self) -> None:
        # E3: multiple active versions + one inactive. E8: scalar ref, array of
        # refs into NS-B, term ref into wip. E13: FTS field. E14 lives on docs.
        # Idempotent: only version up to the target shape; guard by version count.
        #
        # target_templates is a CROSS-namespace pin (NS-B's SAMPLE), which value
        # lookup — scoped to the referencing namespace — cannot resolve; use the
        # canonical template_id.
        sample_id = self._template_id(self.ns_b, "MATRIX_SAMPLE")
        # The wip terminology is likewise cross-namespace — resolve to its id.
        wip_term = self._find_terminology("wip", WIP_TERM_TERMINOLOGY)
        if not wip_term:
            raise RuntimeError(
                f"wip terminology {WIP_TERM_TERMINOLOGY} not found (needed for E8)"
            )
        wip_time_id = wip_term["terminology_id"]
        versions = self._template_versions(self.ns_a, "MATRIX_SPECIMEN")
        base_fields = [
            _f("specimen_code", "string", mandatory=True),
            _f("title", "string"),
            {
                "name": "description",
                "label": "Description",
                "type": "string",
                "full_text_indexed": True,  # E13 (requires reporting.sync_enabled)
            },
            {
                "name": "color",
                "label": "Color",
                "type": "term",
                "terminology_ref": "MATRIX_COLOR",
            },
            {
                "name": "time_unit",
                "label": "Time Unit",
                "type": "term",
                "terminology_ref": wip_time_id,  # E8: term ref into wip (by id)
            },
            {
                "name": "primary_sample",
                "label": "Primary Sample",
                "type": "reference",
                "reference_type": "document",
                "target_templates": [sample_id],  # cross-source pin (NS-B)
                "mandatory": False,
            },
            {
                "name": "linked_samples",
                "label": "Linked Samples",
                "type": "array",
                "array_item_type": "reference",
                "reference_type": "document",  # E8: array-of-refs into NS-B
                "target_templates": [sample_id],
                "mandatory": False,
            },
            {
                "name": "attachment",
                "label": "Attachment",
                "type": "file",
                "mandatory": False,
                "file_config": {"allowed_types": ["*/*"], "max_size_mb": 5},
            },
        ]
        common = {
            "value": "MATRIX_SPECIMEN",
            "label": "Matrix Specimen",
            "namespace": self.ns_a,
            "identity_fields": ["specimen_code"],
            "header_fields": ["specimen_code", "title"],
            "reporting": {"sync_enabled": True},
        }
        if len(versions) < 1:
            self._upsert_template({**common, "fields": base_fields})
        if len(versions) < 2:
            # v2 adds a non-identity field (additive, PoNIF #2 corollary).
            self._upsert_template(
                {**common, "fields": [*base_fields, _f("notes", "string")]}
            )
        if len(versions) < 3:
            # v3 adds another field; retired below (E12 inactive version).
            self._upsert_template(
                {
                    **common,
                    "fields": [*base_fields, _f("notes", "string"), _f("extra", "string")],
                }
            )
        self.report.step("NS-A template MATRIX_SPECIMEN v1-v3 (E3, E8, E13); v3 retired later")

    def _template_event_log(self) -> None:
        # E7: identity-less append-only template.
        self._upsert_template(
            {
                "value": "MATRIX_EVENT_LOG",
                "label": "Matrix Event Log",
                "namespace": self.ns_a,
                "identity_fields": [],  # append-only, no dedup, no PATCH
                "reporting": {"sync_enabled": True},
                "fields": [
                    _f("event_type", "string", mandatory=True),
                    _f("payload", "string"),
                ],
            }
        )
        self.report.step("NS-A template MATRIX_EVENT_LOG (E7: identity-less append-only)")

    # --- documents ---------------------------------------------------------

    def _documents_samples(self) -> None:
        # NS-B samples, no back-ref yet (avoids the cross-namespace doc cycle).
        self._create_documents(
            self.ns_b,
            "MATRIX_SAMPLE",
            [
                {"sample_code": "SAMP-1", "label": "First sample"},
                {"sample_code": "SAMP-2", "label": "Second sample"},
            ],
        )
        self.report.step("NS-B documents: SAMP-1, SAMP-2 (prefixed ids, E11)")

    def _documents_specimens(self) -> None:
        # E8: refs into NS-B (scalar + array) and a wip term. E14: metadata.
        # SPEC-1 gets a second version via PATCH (E6) in _document_backref path.
        #
        # Cross-namespace document refs cannot use bare identifiers (those never
        # cross a namespace boundary, and NS-B's prefixed document_ids aren't
        # UUID7 so they don't hit the direct-id path either). Use the qualified
        # NS:VALUE form, which resolves in the named namespace via the Registry.
        samp1 = self._find_document(self.ns_b, "MATRIX_SAMPLE", "sample_code", "SAMP-1")
        samp2 = self._find_document(self.ns_b, "MATRIX_SAMPLE", "sample_code", "SAMP-2")
        if not samp1 or not samp2:
            raise RuntimeError("NS-B samples must exist before NS-A specimens")
        samp1_id = f"{self.ns_b}:{samp1['document_id']}"
        samp2_id = f"{self.ns_b}:{samp2['document_id']}"
        # Idempotency guard: the SPEC docs pin to latest-active at create time
        # (v3), which _inactivate later retires. Once retired, re-creating would
        # resolve latest = inactive v3 and fail — so create only when absent.
        existing = self._find_document(self.ns_a, "MATRIX_SPECIMEN", "specimen_code", "SPEC-1")
        if existing is None:
            self._create_documents(
                self.ns_a,
                "MATRIX_SPECIMEN",
                [
                    {
                        "data": {
                            "specimen_code": "SPEC-1",
                            "title": "Alpha specimen",
                            "description": "A richly indexed specimen for full-text search",
                            "color": "RED",
                            "time_unit": WIP_TERM_VALUE,
                            "primary_sample": samp1_id,
                            "linked_samples": [samp1_id, samp2_id],
                        },
                        "metadata": {"source": "backup-matrix", "batch": 7},  # E14
                    },
                    {
                        "data": {
                            "specimen_code": "SPEC-2",
                            "title": "Beta specimen",
                            "description": "Second specimen, retired below",
                            "color": "BLUE",
                            "time_unit": WIP_TERM_VALUE,
                        },
                    },
                ],
            )
        # E6: give SPEC-1 a second version via PATCH.
        spec1 = self._find_document(self.ns_a, "MATRIX_SPECIMEN", "specimen_code", "SPEC-1")
        if spec1 and int(spec1.get("version", 1)) < 2:
            self.c.patch(
                "/api/document-store/documents",
                params={"namespace": self.ns_a},
                json_body=[
                    {
                        "document_id": spec1["document_id"],
                        "patch": {"title": "Alpha specimen (rev 2)"},
                    }
                ],
            )
        self.report.step("NS-A documents: SPEC-1 (2 versions E6, refs E8, metadata E14), SPEC-2")

    def _document_backref(self) -> None:
        # Each-way doc reference: NS-B SAMP-1 -> NS-A SPEC-1, added by PATCH now
        # that SPEC-1 exists.
        samp1 = self._find_document(self.ns_b, "MATRIX_SAMPLE", "sample_code", "SAMP-1")
        spec1 = self._find_document(self.ns_a, "MATRIX_SPECIMEN", "specimen_code", "SPEC-1")
        if not samp1 or not spec1:
            self.report.warn("back-ref skipped: SAMP-1 or SPEC-1 not found")
            return
        if samp1.get("data", {}).get("origin_specimen"):
            return  # already linked (idempotent)
        self.c.patch(
            "/api/document-store/documents",
            params={"namespace": self.ns_b},
            json_body=[
                {
                    "document_id": samp1["document_id"],
                    "patch": {"origin_specimen": spec1["document_id"]},
                }
            ],
        )
        self.report.step("NS-B SAMP-1 -> NS-A SPEC-1 back-ref (document ref each way, E8)")

    def _document_edge(self) -> None:
        # E5: a relationship document under the E4 edge type.
        samp1 = self._find_document(self.ns_b, "MATRIX_SAMPLE", "sample_code", "SAMP-1")
        samp2 = self._find_document(self.ns_b, "MATRIX_SAMPLE", "sample_code", "SAMP-2")
        if not samp1 or not samp2:
            self.report.warn("edge doc skipped: samples not found")
            return
        self._create_documents(
            self.ns_b,
            "MATRIX_LINKED_TO",
            [
                {
                    "data": {
                        "source_ref": samp1["document_id"],
                        "target_ref": samp2["document_id"],
                    }
                }
            ],
        )
        self.report.step("NS-B relationship document SAMP-1 -> SAMP-2 (E5)")

    def _documents_event_log(self) -> None:
        # E7: append-only. Idempotent guard — only seed if empty, since every
        # POST appends a new document (no dedup).
        existing = self._count_documents(self.ns_a, "MATRIX_EVENT_LOG")
        if existing >= 3:
            self.report.step(f"NS-A EVENT_LOG already has {existing} docs (skip)")
            return
        self._create_documents(
            self.ns_a,
            "MATRIX_EVENT_LOG",
            [
                {"data": {"event_type": "created", "payload": "one"}},
                {"data": {"event_type": "updated", "payload": "two"}},
                {"data": {"event_type": "archived", "payload": "three"}},
            ],
        )
        self.report.step("NS-A EVENT_LOG: 3 append-only documents (E7)")

    # --- files (E9) --------------------------------------------------------

    def _files(self) -> None:
        blob = b"backup-matrix fixture attachment payload\n"
        try:
            resp = self.c.post(
                "/api/document-store/files",
                files={"file": ("matrix-attachment.txt", blob, "text/plain")},
                data={"namespace": self.ns_a, "description": "matrix fixture blob"},
            )
        except ApiError as exc:
            if exc.status == 503:
                self.report.warn(
                    "E9 files SKIPPED: file storage disabled on target "
                    "(WIP_FILE_STORAGE_ENABLED not set). Blobs/file-ref cells "
                    "will be inapplicable."
                )
                return
            raise
        file_id = resp.get("file_id")
        self.report.file_storage_enabled = True
        # Link the blob into SPEC-1's file field.
        spec1 = self._find_document(self.ns_a, "MATRIX_SPECIMEN", "specimen_code", "SPEC-1")
        if spec1 and not spec1.get("data", {}).get("attachment"):
            self.c.patch(
                "/api/document-store/documents",
                params={"namespace": self.ns_a},
                json_body=[
                    {"document_id": spec1["document_id"], "patch": {"attachment": file_id}}
                ],
            )
        self.report.step(f"NS-A file {file_id} uploaded + linked to SPEC-1 (E9)")

    # --- custom synonym (E10) ---------------------------------------------

    def _custom_synonym(self) -> None:
        term = self._find_terminology(self.ns_a, "MATRIX_COLOR")
        if not term:
            self.report.warn("E10 synonym skipped: MATRIX_COLOR not found")
            return
        target_id = term["terminology_id"]
        resp = self.c.post(
            "/api/registry/synonyms/add",
            json_body=[
                {
                    "target_id": target_id,
                    "synonym_namespace": self.ns_a,
                    "synonym_entity_type": "terminologies",
                    "synonym_composite_key": {"legacy_code": "COLOURS"},
                    "created_by": self.created_by,
                }
            ],
        )
        results = resp.get("results", []) if isinstance(resp, dict) else []
        status = results[0].get("status") if results else "?"
        self.report.step(f"NS-A custom synonym on MATRIX_COLOR (E10): {status}")

    # --- inactivation (E12) ------------------------------------------------

    def _inactivate(self) -> None:
        # Retire one term (MATRIX_COLOR/GREY), one template version
        # (MATRIX_SPECIMEN v3), and archive one document (SPEC-2).
        grey = self._find_term(self.ns_a, "MATRIX_COLOR", "GREY")
        if grey and grey.get("status") == "active":
            check_bulk(
                self.c.post(
                    "/api/def-store/terms/deprecate",
                    params={"namespace": self.ns_a},
                    json_body=[
                        {"term_id": grey["term_id"], "reason": "matrix E12 inactive term"}
                    ],
                ),
                context="deprecate MATRIX_COLOR/GREY",
            )
            self.report.step("NS-A term MATRIX_COLOR/GREY deprecated (E12)")

        # v3 carries the SPEC documents (they pinned to latest-active at create
        # time), so force=true is required — and the result is exactly the
        # CASE-766 cell-zero shape: documents pinned to an INACTIVE version.
        versions = self._template_versions(self.ns_a, "MATRIX_SPECIMEN")
        v3 = next((v for v in versions if v.get("version") == 3), None)
        if v3 and v3.get("status") == "active":
            check_bulk(
                self.c.delete(
                    "/api/template-store/templates",
                    json_body=[
                        {
                            "id": v3["template_id"],
                            "version": 3,
                            "force": True,
                            "updated_by": self.created_by,
                        }
                    ],
                ),
                context="deactivate MATRIX_SPECIMEN v3",
            )
            self.report.step(
                "NS-A template MATRIX_SPECIMEN v3 deactivated with dependents "
                "(E12 inactive version + CASE-766 cell-zero shape)"
            )

        spec2 = self._find_document(self.ns_a, "MATRIX_SPECIMEN", "specimen_code", "SPEC-2")
        if spec2 and spec2.get("status") == "active":
            check_bulk(
                self.c.post(
                    "/api/document-store/documents/archive",
                    json_body=[{"id": spec2["document_id"], "archived_by": self.created_by}],
                ),
                context="archive SPEC-2",
            )
            self.report.step("NS-A document SPEC-2 archived (E12)")

    # --------------------------------------------------------------- teardown

    def teardown(self) -> list[str]:
        """Delete the fixture namespaces (they are deletion_mode:full).

        A namespace delete cascades to every entity in it, so this is the whole
        cleanup — the same straight-delete the layer-L runner relies on. NS-A is
        deleted first so its cross-namespace refs into NS-B are gone before NS-B.
        """
        removed = []
        for ns in (self.ns_a, self.ns_b):
            try:
                self.c.delete(
                    f"/api/registry/namespaces/{ns}",
                    params={"force": True, "deleted_by": self.created_by},
                )
                removed.append(ns)
            except ApiError as exc:
                if exc.status == 404:
                    continue  # already gone — idempotent
                raise
        return removed

    # ------------------------------------------------------------------ count

    def count(self) -> dict[str, Any]:
        """Read every entity class back → the EXPECTED_COUNTS baseline (X-02)."""
        return {
            "target": self.c.target.source,
            "namespaces": {
                self.ns_a: self._count_namespace(self.ns_a),
                self.ns_b: self._count_namespace(self.ns_b),
            },
        }

    def _count_namespace(self, ns: str) -> dict[str, Any]:
        terminologies = self._get_total(
            "/api/def-store/terminologies", {"namespace": ns, "page_size": 1000}
        )
        term_relations = self._get_total(
            "/api/def-store/ontology/term-relations/all",
            {"namespace": ns, "status": "active", "page_size": 1000},
        )
        # Templates: entity templates vs edge types, active versions vs all.
        tpl_active = self.c.get(
            "/api/template-store/templates",
            params={"namespace": ns, "status": "active", "page_size": 1000},
        )
        tpl_all = self.c.get(
            "/api/template-store/templates",
            params={"namespace": ns, "page_size": 1000},
        )
        edge_types = sum(
            1
            for t in tpl_all.get("items", [])
            if t.get("usage") == "relationship"
        )
        # Documents, split by status so the conservation asserts are unambiguous.
        # latest_only WITHOUT a status filter includes archived — pass the
        # status explicitly for each bucket.
        docs_active = self._get_total(
            "/api/document-store/documents",
            {"namespace": ns, "latest_only": True, "status": "active", "page_size": 1000},
        )
        docs_archived = self._get_total(
            "/api/document-store/documents",
            {"namespace": ns, "latest_only": True, "status": "archived", "page_size": 1000},
        )
        # Every version, every status — the all_versions sync-strategy baseline.
        doc_versions_total = self._count_all_doc_versions(ns)
        files = self._get_total(
            "/api/document-store/files", {"namespace": ns, "page_size": 100}
        )
        registry_total, synonyms = self._count_registry(ns)

        # Sum terms across this namespace's terminologies (active only).
        term_total = self._count_terms(ns)

        return {
            "terminologies": terminologies,
            "terms_active": term_total,
            "term_relations": term_relations,
            "templates_active_versions": tpl_active.get("total", 0),
            "templates_all_versions": tpl_all.get("total", 0),
            "edge_types": edge_types,
            "documents_active_latest": docs_active,
            "documents_archived_latest": docs_archived,
            "document_versions_total": doc_versions_total,
            "files": files,
            "registry_entries": registry_total,
            "registry_synonyms": synonyms,
        }

    def _count_all_doc_versions(self, ns: str) -> int:
        """Count every document version in a namespace, all statuses (paged)."""
        total = 0
        page = 1
        while True:
            resp = self.c.post(
                "/api/document-store/documents/query",
                params={"namespace": ns},
                json_body={"status": None, "page": page, "page_size": 100},
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            total += len(items)
            pages = resp.get("pages", 1) if isinstance(resp, dict) else 1
            if page >= pages or not items:
                break
            page += 1
        return total

    def _count_registry(self, ns: str) -> tuple[int, int]:
        """Total registry entries + summed custom synonyms for a namespace."""
        total = 0
        synonyms = 0
        page = 1
        while True:
            resp = self.c.get(
                "/api/registry/entries",
                params={"namespace": ns, "page": page, "page_size": 100},
            )
            total = resp.get("total", 0)
            for it in resp.get("items", []):
                synonyms += int(it.get("synonyms_count", 0))
            if page >= resp.get("pages", 1):
                break
            page += 1
        return total, synonyms

    def _count_terms(self, ns: str) -> int:
        terms = 0
        page = 1
        while True:
            resp = self.c.get(
                "/api/def-store/terminologies",
                params={"namespace": ns, "page": page, "page_size": 100},
            )
            items = resp.get("items", [])
            for term_def in items:
                tid = term_def["terminology_id"]
                tr = self.c.get(
                    f"/api/def-store/terminologies/{tid}/terms",
                    params={"namespace": ns, "status": "active", "page_size": 1},
                )
                terms += tr.get("total", 0)
            if page >= resp.get("pages", 1):
                break
            page += 1
        return terms

    # ------------------------------------------------------------- API helpers

    def _create_terminology(self, ns: str, value: str, label: str) -> None:
        check_bulk(
            self.c.post(
                "/api/def-store/terminologies",
                params={"on_conflict": "validate"},
                json_body=[
                    {
                        "value": value,
                        "label": label,
                        "namespace": ns,
                        "created_by": self.created_by,
                    }
                ],
            ),
            context=f"create terminology {ns}/{value}",
        )

    def _create_terms(self, ns: str, terminology_value: str, terms: list[dict]) -> None:
        for t in terms:
            t.setdefault("created_by", self.created_by)
        check_bulk(
            self.c.post(
                f"/api/def-store/terminologies/{terminology_value}/terms",
                params={"namespace": ns, "on_conflict": "validate"},
                json_body=terms,
            ),
            context=f"create terms {ns}/{terminology_value}",
        )

    def _upsert_template(self, tpl: dict) -> None:
        tpl.setdefault("created_by", self.created_by)
        check_bulk(
            self.c.post("/api/template-store/templates", json_body=[tpl]),
            context=f"upsert template {tpl['namespace']}/{tpl['value']}",
        )

    def _create_documents(self, ns: str, template_value: str, docs: list[dict]) -> None:
        # Accept either bare data dicts or {data, metadata} envelopes.
        body = []
        for d in docs:
            if "data" in d and isinstance(d["data"], dict):
                item = {"template_id": template_value, "namespace": ns, **d}
            else:
                item = {"template_id": template_value, "namespace": ns, "data": d}
            item.setdefault("created_by", self.created_by)
            body.append(item)
        check_bulk(
            self.c.post("/api/document-store/documents", json_body=body),
            context=f"create documents {ns}/{template_value}",
        )

    def _template_exists(self, ns: str, value: str) -> bool:
        resp = self.c.get(
            "/api/template-store/templates",
            params={"namespace": ns, "value": value, "page_size": 1},
        )
        return resp.get("total", 0) > 0

    def _template_id(self, ns: str, value: str) -> str:
        resp = self.c.get(
            "/api/template-store/templates",
            params={"namespace": ns, "value": value, "latest_only": True, "page_size": 1},
        )
        items = resp.get("items", [])
        if not items:
            raise RuntimeError(f"template {ns}/{value} not found (needed for cross-ns pin)")
        return items[0]["template_id"]

    def _template_versions(self, ns: str, value: str) -> list[dict]:
        if not self._template_exists(ns, value):
            return []
        resp = self.c.get(
            f"/api/template-store/templates/by-value/{value}/versions",
            params={"namespace": ns},
        )
        return resp.get("items", [])

    def _find_terminology(self, ns: str, value: str) -> dict | None:
        resp = self.c.get(
            "/api/def-store/terminologies", params={"namespace": ns, "value": value}
        )
        items = resp.get("items", [])
        return items[0] if items else None

    def _find_term(self, ns: str, terminology_value: str, term_value: str) -> dict | None:
        term = self._find_terminology(ns, terminology_value)
        if not term:
            return None
        resp = self.c.get(
            f"/api/def-store/terminologies/{term['terminology_id']}/terms",
            params={"namespace": ns, "search": term_value, "page_size": 100},
        )
        for t in resp.get("items", []):
            if t.get("value") == term_value:
                return t
        return None

    def _find_document(
        self, ns: str, template_value: str, field_name: str, field_value: str
    ) -> dict | None:
        resp = self.c.post(
            "/api/document-store/documents/query",
            params={"namespace": ns},
            json_body={
                "template_id": template_value,
                "filters": [
                    {"field": f"data.{field_name}", "operator": "eq", "value": field_value}
                ],
                "page_size": 5,
            },
        )
        items = resp.get("items", []) if isinstance(resp, dict) else []
        return items[0] if items else None

    def _count_documents(self, ns: str, template_value: str) -> int:
        return self._get_total(
            "/api/document-store/documents",
            {"namespace": ns, "template_value": template_value, "page_size": 1},
        )

    def _get_total(self, path: str, params: dict) -> int:
        resp = self.c.get(path, params=params)
        if isinstance(resp, dict):
            return resp.get("total", 0)
        return 0


def _f(name: str, type_: str, *, mandatory: bool = False) -> dict:
    """A minimal field definition (label defaults to a title-cased name)."""
    return {
        "name": name,
        "label": name.replace("_", " ").title(),
        "type": type_,
        "mandatory": mandatory,
    }
