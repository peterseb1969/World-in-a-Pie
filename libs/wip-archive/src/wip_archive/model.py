"""Offline analysis model over a backup archive.

``ArchiveModel`` loads an archive into an entity index plus a dependency
graph and computes the analyses the toolkit's read and shaping verbs share:
degrees and closures per template, islands, edge-type connectivity, health
stats, and structured findings. ``inspect`` is its read-only face; later
``verify`` gates on the same findings and ``filter`` walks the same closures,
so a transform's dry run is an analysis view rather than a parallel code path
that can drift from what the transform actually does.

**Vocabulary.** "Edge" is reserved here for the platform's meaning: an *edge
type* is a template declaring ``usage: "relationship"``, and a *relationship
document* is an instance of one. The dependency graph this model builds
contains far more than those — every document-to-document reference field, every
term and file reference, every template pin is a link in it. Those are called
**references** (``reference_edges``, "declared references", "reference graph"),
never edges, because an operator asking "how many edges does this archive have"
means relationship documents and would be misled by a number that silently
counted plain reference fields too.

Two boundaries are deliberate:

- **Archives only.** Nothing here talks to a running instance. Analysing an
  instance means taking a backup and analysing that; a live mode would be a
  second definition of "what a namespace contains", which is exactly the
  duplication the server backup engine exists to remove.
- **Read only.** The model never mutates an archive. Transforms build on it
  but own their own writes.

Memory: definitions (templates, terminologies, terms, files, registry rows)
are held in full — they are small and every analysis needs random access to
them. Documents are streamed twice and never retained: pass 1 records each
document's template pin and highest version, pass 2 accumulates references into
counters. Peak memory is therefore O(definitions + document ids), not
O(archive), which keeps the model usable on archives far larger than RAM.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .archive import ENTITY_FILES, ArchiveReader
from .models import Manifest

# Version of the machine-readable output contract (`to_dict`, and the CLI's
# `inspect --json`). Consumers branch on it. Additive fields keep the version;
# removing or re-typing a field is a major bump.
SCHEMA_VERSION = "1.0"

# Entity types that carry a Registry identity row. Mirrors the restore door's
# own list: these are the rows whose absence makes a restored namespace look
# healthy on list surfaces while every id-based read fails.
IDENTITY_BEARING_ENTITY_TYPES = (
    "terminologies",
    "terms",
    "term_relations",
    "templates",
    "documents",
    "files",
)

# Why a reference target is not in the archive.
EXTERNAL_OTHER_NAMESPACE = "other-namespace-not-in-archive"
EXTERNAL_UNKNOWN = "not-in-archive"


@dataclass(frozen=True)
class Finding:
    """One structured problem statement produced by an analysis.

    ``inspect`` reports findings; ``verify`` (M2) filters them to
    restorability and turns them into an exit code. Both read the same
    objects, so a problem cannot be visible to one and invisible to the
    other.
    """

    severity: str  # "error" | "warning" | "info"
    finding_class: str
    subject: str
    detail: str
    count: int = 1
    samples: tuple[str, ...] = ()

    # Most-severe-first ordering for reports.
    _RANK: ClassVar[dict[str, int]] = {"error": 0, "warning": 1, "info": 2}

    @property
    def rank(self) -> int:
        return self._RANK.get(self.severity, 99)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "class": self.finding_class,
            "subject": self.subject,
            "detail": self.detail,
            "count": self.count,
            "samples": list(self.samples),
        }


@dataclass
class ExternalRef:
    """A reference whose target is not in the archive, with why."""

    kind: str  # "template" | "terminology" | "document" | "term" | "file"
    target_id: str
    reason: str
    referenced_by: str
    # Which template the reference originates from, when known. Closure
    # reports are per-selection, so an external reference held by some other
    # template must not appear in this selection's external list.
    origin_template: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "reason": self.reason,
            "referenced_by": self.referenced_by,
        }


@dataclass
class TemplateVersionNode:
    """One template version — the granularity declared references live at.

    Declared references are a property of a *version*: v1 may reference a
    terminology that v2 dropped. Rolling them up to the template would report
    a dependency the current schema no longer has.
    """

    namespace: str
    template_id: str
    version: int
    value: str = ""
    label: str = ""
    usage: str = "entity"
    versioned: bool = True
    status: str = ""
    identity_fields: tuple[str, ...] = ()
    field_count: int = 0
    extends: str | None = None
    declared_templates: set[str] = field(default_factory=set)
    declared_terminologies: set[str] = field(default_factory=set)
    # Template-level endpoint allow-lists on a relationship template. The
    # retired closure walker read only field-level references and never these,
    # so an edge type's declared endpoints were invisible to it.
    endpoint_source_templates: set[str] = field(default_factory=set)
    endpoint_target_templates: set[str] = field(default_factory=set)
    # Declared references the archive cannot resolve — no template or
    # terminology it carries answers to that id, value, or qualified name.
    unresolved_templates: set[str] = field(default_factory=set)
    unresolved_terminologies: set[str] = field(default_factory=set)

    @property
    def is_edge_type(self) -> bool:
        return self.usage == "relationship"

    def all_declared_templates(self) -> set[str]:
        return (
            self.declared_templates
            | self.endpoint_source_templates
            | self.endpoint_target_templates
        )


@dataclass
class TemplateNode:
    """A template, rolled up across its versions."""

    namespace: str
    template_id: str
    value: str = ""
    versions: dict[int, TemplateVersionNode] = field(default_factory=dict)
    # Documents pinned to this template, and how they spread over its
    # versions: the migration-pressure number.
    document_ids: set[str] = field(default_factory=set)
    document_version_count: int = 0
    docs_per_template_version: Counter = field(default_factory=Counter)

    @property
    def latest_version(self) -> TemplateVersionNode | None:
        if not self.versions:
            return None
        return self.versions[max(self.versions)]

    @property
    def usage(self) -> str:
        latest = self.latest_version
        return latest.usage if latest else "entity"

    @property
    def versioned(self) -> bool:
        latest = self.latest_version
        return latest.versioned if latest else True

    @property
    def is_edge_type(self) -> bool:
        return self.usage == "relationship"

    @property
    def label(self) -> str:
        """Human-facing name: the template value, falling back to its id."""
        return self.value or self.template_id


@dataclass
class TerminologyNode:
    namespace: str
    terminology_id: str
    value: str = ""
    status: str = ""
    mutable: bool = False
    term_ids: set[str] = field(default_factory=set)
    deprecated_term_ids: set[str] = field(default_factory=set)
    inactive_term_ids: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return self.value or self.terminology_id


@dataclass
class TermNode:
    namespace: str
    term_id: str
    terminology_id: str
    value: str = ""
    status: str = "active"
    aliases: tuple[str, ...] = ()
    relation_degree: int = 0


@dataclass
class FileNode:
    namespace: str
    file_id: str
    filename: str = ""
    size_bytes: int = 0
    status: str = ""


@dataclass
class DocumentEntry:
    """What the model retains per document — never the document itself."""

    namespace: str
    template_id: str
    max_version: int
    version_count: int = 0
    has_identity: bool = False


@dataclass
class ReferenceGraph:
    """Actual references between documents, aggregated as counts.

    Counts, not per-document records: the graph must stay O(templates) while
    the archive it summarises may be far larger than memory.

    One instance per closure variant — with-history and latest-only — because
    the ``latest-only`` transform changes the graph and an operator choosing it
    needs both numbers, not one of them.

    Vocabulary, deliberately kept apart (see the module docstring):
    ``reference_edges`` counts EVERY document-to-document reference, whether it
    came from a plain reference field or from a relationship document, because
    that is what closures and islands are computed over. The
    ``relationship_*`` counters are the narrower platform sense — instances of
    a template whose ``usage`` is ``relationship``.
    """

    # (source template_id, target template_id) -> number of document references
    reference_edges: Counter = field(default_factory=Counter)
    # (template_id, terminology_id) -> number of term references
    terminology_use: Counter = field(default_factory=Counter)
    # term_id -> number of references
    term_use: Counter = field(default_factory=Counter)
    # file_id -> number of references
    file_use: Counter = field(default_factory=Counter)
    # (template_id, template_version) -> distinct documents pinned
    pins: Counter = field(default_factory=Counter)
    # Documents reached by relationship documents of a given edge type:
    # (edge type template_id, endpoint role) -> set of endpoint document ids
    relationship_endpoints: dict[tuple[str, str], set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    # edge type template_id -> number of relationship documents
    relationship_docs: Counter = field(default_factory=Counter)

    # Adjacency indexes, built once on first use. Every per-template analysis
    # asks for neighbours, so scanning the reference counter per question would
    # make the whole report quadratic in the number of templates.
    _out: dict[str, set[str]] | None = None
    _in: dict[str, set[str]] | None = None
    _terminologies_of: dict[str, set[str]] | None = None

    def _build_indexes(self) -> None:
        out: dict[str, set[str]] = defaultdict(set)
        inbound: dict[str, set[str]] = defaultdict(set)
        for (src, dst) in self.reference_edges:
            out[src].add(dst)
            inbound[dst].add(src)
        terminologies: dict[str, set[str]] = defaultdict(set)
        for (tpl_id, terminology_id) in self.terminology_use:
            terminologies[tpl_id].add(terminology_id)
        self._out, self._in, self._terminologies_of = out, inbound, terminologies

    def out_neighbours(self, template_id: str) -> set[str]:
        if self._out is None:
            self._build_indexes()
        assert self._out is not None
        return self._out.get(template_id, set())

    def in_neighbours(self, template_id: str) -> set[str]:
        if self._in is None:
            self._build_indexes()
        assert self._in is not None
        return self._in.get(template_id, set())

    def terminologies_of(self, template_id: str) -> set[str]:
        if self._terminologies_of is None:
            self._build_indexes()
        assert self._terminologies_of is not None
        return self._terminologies_of.get(template_id, set())


@dataclass
class Island:
    """A connected component of templates — a minimal extraction unit.

    Components are computed over template-to-template references (declared and
    actual, treated as undirected). Terminologies attach to the islands whose
    templates reference them rather than joining the components themselves:
    a vocabulary shared by two otherwise unrelated template sets should not
    weld them into one extraction unit, it should be reported as duplicated
    into both extractions (and reconciled by identity on the way back).
    """

    index: int
    template_ids: set[str]
    terminology_ids: set[str]

    @property
    def size(self) -> int:
        return len(self.template_ids)


@dataclass
class ClosureResult:
    """What a selection drags along with it."""

    template_ids: set[str]
    terminology_ids: set[str]
    external: list[ExternalRef]

    def to_dict(self, model: ArchiveModel) -> dict[str, Any]:
        return {
            "templates": sorted(model.template_label(t) for t in self.template_ids),
            "terminologies": sorted(
                model.terminology_label(t) for t in self.terminology_ids
            ),
            "external": [e.to_dict() for e in self.external],
        }


@dataclass
class TemplateReport:
    """The per-template answer to both the extraction and deletion questions.

    They are asymmetric and the report keeps them apart: extracting a template
    needs its outgoing references satisfied (both closures), deleting it needs its
    in-degree to be zero. Conflating them is how a "safe to remove" call gets
    made from the wrong number.
    """

    template_id: str
    label: str
    namespace: str
    usage: str
    versioned: bool
    document_count: int
    document_version_count: int
    docs_per_template_version: dict[int, int]
    in_degree_declared: int
    in_degree_actual: int
    out_degree_declared: int
    out_degree_actual: int
    incoming_declared: list[str]
    incoming_actual: list[str]
    schema_closure: ClosureResult
    data_closure: ClosureResult
    data_closure_latest_only: ClosureResult
    island: int | None

    def to_dict(self, model: ArchiveModel) -> dict[str, Any]:
        return {
            "template": self.label,
            "template_id": self.template_id,
            "namespace": self.namespace,
            "usage": self.usage,
            "versioned": self.versioned,
            "documents": self.document_count,
            "document_versions": self.document_version_count,
            "docs_per_template_version": self.docs_per_template_version,
            "in_degree": {
                "declared": self.in_degree_declared,
                "actual": self.in_degree_actual,
                "declared_by": self.incoming_declared,
                "referenced_by": self.incoming_actual,
            },
            "out_degree": {
                "declared": self.out_degree_declared,
                "actual": self.out_degree_actual,
            },
            "schema_closure": self.schema_closure.to_dict(model),
            "data_closure": self.data_closure.to_dict(model),
            "data_closure_latest_only": self.data_closure_latest_only.to_dict(model),
            "island": self.island,
        }


@dataclass
class EdgeTypeReport:
    """Connectivity of one edge type, quantified.

    ``/relationships`` and ``/traverse`` are application-visible features, so
    dropping an edge type changes what an app can do — not merely how complete
    the archive is. The disconnection percentages are what makes that
    consequence visible before the decision.
    """

    template_id: str
    label: str
    namespace: str
    versioned: bool
    declared_source_templates: list[str]
    declared_target_templates: list[str]
    relationship_documents: int
    relationship_documents_latest_only: int
    endpoints_touched: dict[str, int]
    disconnection: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_type": self.label,
            "template_id": self.template_id,
            "namespace": self.namespace,
            "versioned": self.versioned,
            "declared_endpoints": {
                "source_templates": self.declared_source_templates,
                "target_templates": self.declared_target_templates,
            },
            "relationship_documents": self.relationship_documents,
            "relationship_documents_latest_only": (
                self.relationship_documents_latest_only
            ),
            "endpoints_touched": self.endpoints_touched,
            "disconnection": self.disconnection,
        }


class ArchiveModel:
    """An archive's entity index, dependency graph, and analyses."""

    def __init__(self, manifest: Manifest, manifest_raw: dict[str, Any]) -> None:
        self.manifest = manifest
        self.manifest_raw = manifest_raw
        self.namespaces: list[str] = []
        self.templates: dict[str, TemplateNode] = {}
        self.terminologies: dict[str, TerminologyNode] = {}
        self.terms: dict[str, TermNode] = {}
        self.files: dict[str, FileNode] = {}
        self.documents: dict[str, DocumentEntry] = {}
        self.term_relation_count: int = 0
        # namespace -> entity_type -> count, and the registry rows per namespace
        self.entity_counts: dict[str, dict[str, int]] = {}
        self.registry_ids: dict[str, set[str]] = {}
        self.blob_ids: set[str] = set()
        self.include_files: bool = False
        self.history = ReferenceGraph()
        self.latest = ReferenceGraph()
        self.external_refs: list[ExternalRef] = []
        self.dangling: list[ExternalRef] = []
        self._islands: list[Island] | None = None
        self._island_of: dict[str, int] = {}

    # ---------------------------------------------------------------- load

    @classmethod
    def load(
        cls,
        reader: ArchiveReader,
        *,
        namespaces: list[str] | None = None,
    ) -> ArchiveModel:
        """Build the model from an open archive reader.

        ``namespaces`` restricts the load to a subset; by default every
        namespace in the archive is indexed, and references between them count as
        internal — a multi-namespace backup is one analysis subject.
        """
        model = cls(reader.read_manifest(), reader.read_manifest_raw())
        model.include_files = model.manifest.include_files
        available = reader.list_namespaces()
        model.namespaces = [
            ns for ns in available if namespaces is None or ns in namespaces
        ]
        model.blob_ids = set(reader.list_blobs())

        for ns in model.namespaces:
            model._load_definitions(reader, ns)
        model._resolve_declared_references()
        for ns in model.namespaces:
            model._index_documents(reader, ns)
        model._attach_documents_to_templates()
        for ns in model.namespaces:
            model._extract_reference_edges(reader, ns)

        model._classify_declared_references()
        return model

    def _load_definitions(self, reader: ArchiveReader, ns: str) -> None:
        counts: dict[str, int] = {}

        for row in reader.read_entities("terminologies", namespace=ns):
            tid = row.get("terminology_id")
            if not tid:
                continue
            self.terminologies[tid] = TerminologyNode(
                namespace=ns,
                terminology_id=tid,
                value=row.get("value", "") or "",
                status=str(row.get("status", "") or ""),
                mutable=bool(row.get("mutable", False)),
            )
            counts["terminologies"] = counts.get("terminologies", 0) + 1

        for row in reader.read_entities("terms", namespace=ns):
            term_id = row.get("term_id")
            if not term_id:
                continue
            terminology_id = row.get("terminology_id", "") or ""
            status = str(row.get("status", "active") or "active")
            self.terms[term_id] = TermNode(
                namespace=ns,
                term_id=term_id,
                terminology_id=terminology_id,
                value=row.get("value", "") or "",
                status=status,
                aliases=tuple(row.get("aliases") or ()),
            )
            parent = self.terminologies.get(terminology_id)
            if parent is not None:
                parent.term_ids.add(term_id)
                if status == "deprecated":
                    parent.deprecated_term_ids.add(term_id)
                elif status == "inactive":
                    parent.inactive_term_ids.add(term_id)
            counts["terms"] = counts.get("terms", 0) + 1

        # Ontology relations are counted, and their degree is attributed to the
        # terms they touch, so a vocabulary dive can show which terms carry
        # structure. The relations themselves are not retained: nothing in M1
        # traverses them, and a hierarchy can be large.
        for row in reader.read_entities("term_relations", namespace=ns):
            self.term_relation_count += 1
            counts["term_relations"] = counts.get("term_relations", 0) + 1
            for key in ("source_term_id", "target_term_id"):
                endpoint = self.terms.get(row.get(key) or "")
                if endpoint is not None:
                    endpoint.relation_degree += 1

        for row in reader.read_entities("templates", namespace=ns):
            node = self._template_version_from_row(ns, row)
            if node is None:
                continue
            tpl = self.templates.setdefault(
                node.template_id,
                TemplateNode(
                    namespace=ns, template_id=node.template_id, value=node.value
                ),
            )
            # A later version's value wins: it is the template's current name.
            if node.value and node.version >= max(tpl.versions or [0]):
                tpl.value = node.value
            tpl.versions[node.version] = node
            counts["templates"] = counts.get("templates", 0) + 1

        for row in reader.read_entities("files", namespace=ns):
            file_id = row.get("file_id")
            if not file_id:
                continue
            self.files[file_id] = FileNode(
                namespace=ns,
                file_id=file_id,
                filename=row.get("filename", "") or "",
                size_bytes=int(row.get("size_bytes", 0) or 0),
                status=str(row.get("status", "") or ""),
            )
            counts["files"] = counts.get("files", 0) + 1

        registry_ids: set[str] = set()
        for row in reader.read_entities("registry_entries", namespace=ns):
            entry_id = row.get("entry_id")
            if entry_id:
                registry_ids.add(entry_id)
            counts["registry_entries"] = counts.get("registry_entries", 0) + 1
        self.registry_ids[ns] = registry_ids
        self.entity_counts[ns] = counts

    @staticmethod
    def _template_version_from_row(
        ns: str, row: dict[str, Any]
    ) -> TemplateVersionNode | None:
        template_id = row.get("template_id")
        if not template_id:
            return None
        node = TemplateVersionNode(
            namespace=ns,
            template_id=template_id,
            version=int(row.get("version", 1) or 1),
            value=row.get("value", "") or "",
            label=row.get("label", "") or "",
            usage=str(row.get("usage", "entity") or "entity"),
            versioned=bool(row.get("versioned", True)),
            status=str(row.get("status", "") or ""),
            identity_fields=tuple(row.get("identity_fields") or ()),
            extends=row.get("extends") or None,
        )
        if node.extends:
            node.declared_templates.add(node.extends)

        fields = row.get("fields") or []
        node.field_count = len(fields)
        for f in fields:
            for key in ("terminology_ref", "array_terminology_ref"):
                ref = f.get(key)
                if ref:
                    node.declared_terminologies.add(ref)
            for key in ("template_ref", "array_template_ref"):
                ref = f.get(key)
                if ref:
                    node.declared_templates.add(ref)
            for ref in f.get("target_templates") or []:
                node.declared_templates.add(ref)
            for ref in f.get("target_terminologies") or []:
                node.declared_terminologies.add(ref)

        # Template-level endpoint lists exist only on relationship templates.
        # An entry is either the two-half {lookup_value, resolved} shape
        # (format 3.1+) or a legacy bare string; either way the model keeps
        # one string handle per endpoint — the resolved id when present,
        # else the lookup — and the downstream declared-reference resolution
        # already accepts id, bare value, and ns:VALUE alike.
        def _endpoint_handle(entry: Any) -> str | None:
            if isinstance(entry, dict):
                return entry.get("resolved") or entry.get("lookup_value")
            return entry if isinstance(entry, str) else None

        if node.is_edge_type:
            node.endpoint_source_templates.update(
                h for e in (row.get("source_templates") or [])
                if (h := _endpoint_handle(e))
            )
            node.endpoint_target_templates.update(
                h for e in (row.get("target_templates") or [])
                if (h := _endpoint_handle(e))
            )
        return node

    def _resolve_declared_references(self) -> None:
        """Turn declared reference strings into the ids they name.

        A template stores its references in whatever form the caller wrote
        them: a canonical id, a bare value, or a qualified ``ns:VALUE``. The
        platform resolves all three through the Registry, and the same
        reference is the same link whichever form it took — so a model that
        only understood ids would report a template's real dependencies as
        pointing outside the archive.

        Offline, resolution is limited to what the archive carries: ids and
        values it holds. A bare value resolves within the referring
        template's own namespace and a qualified one crosses, exactly as the
        platform scopes them; anything else stays unresolved and is reported
        as external, which is the honest answer — the door will resolve it on
        the target instance, or fail there.
        """
        templates_by_value: dict[tuple[str, str], str] = {}
        for template_id, tpl in self.templates.items():
            if tpl.value:
                templates_by_value[(tpl.namespace, tpl.value)] = template_id
        terminologies_by_value: dict[tuple[str, str], str] = {}
        for terminology_id, node in self.terminologies.items():
            if node.value:
                terminologies_by_value[(node.namespace, node.value)] = terminology_id

        def resolve(ref: str, namespace: str, index: dict, known: dict) -> str | None:
            if ref in known:
                return ref
            if ":" in ref:
                ref_ns, value = ref.split(":", 1)
                return index.get((ref_ns, value))
            return index.get((namespace, ref))

        for tpl in self.templates.values():
            for node in tpl.versions.values():
                for attr, index, known in (
                    ("declared_templates", templates_by_value, self.templates),
                    ("endpoint_source_templates", templates_by_value, self.templates),
                    ("endpoint_target_templates", templates_by_value, self.templates),
                    (
                        "declared_terminologies",
                        terminologies_by_value,
                        self.terminologies,
                    ),
                ):
                    resolved: set[str] = set()
                    for ref in getattr(node, attr):
                        target = resolve(ref, node.namespace, index, known)
                        if target is not None:
                            resolved.add(target)
                        elif index is templates_by_value:
                            node.unresolved_templates.add(ref)
                        else:
                            node.unresolved_terminologies.add(ref)
                    setattr(node, attr, resolved)

    def _index_documents(self, reader: ArchiveReader, ns: str) -> None:
        """Pass 1: record each document's template pin and highest version."""
        count = 0
        for row in reader.read_entities("documents", namespace=ns):
            count += 1
            doc_id = row.get("document_id")
            if not doc_id:
                continue
            version = int(row.get("version", 1) or 1)
            entry = self.documents.get(doc_id)
            if entry is None:
                self.documents[doc_id] = DocumentEntry(
                    namespace=ns,
                    template_id=row.get("template_id", "") or "",
                    max_version=version,
                    version_count=1,
                    has_identity=bool(row.get("identity_hash")),
                )
            else:
                entry.version_count += 1
                if version > entry.max_version:
                    entry.max_version = version
                    entry.template_id = row.get("template_id", "") or ""
                    entry.has_identity = bool(row.get("identity_hash"))
        self.entity_counts.setdefault(ns, {})["documents"] = count

    def _attach_documents_to_templates(self) -> None:
        """Roll the document index up to templates, once every pass 1 is done.

        Done after all namespaces are indexed rather than inside each one: a
        document's template pin is read from its highest version, which may
        arrive in any order, and re-walking the whole index per namespace
        would make loading quadratic in namespace count.
        """
        for doc_id, entry in self.documents.items():
            tpl = self.templates.get(entry.template_id)
            if tpl is not None:
                tpl.document_ids.add(doc_id)

    def _extract_reference_edges(self, reader: ArchiveReader, ns: str) -> None:
        """Pass 2: accumulate actual references; retain nothing per document."""
        for row in reader.read_entities("documents", namespace=ns):
            doc_id = row.get("document_id")
            if not doc_id:
                continue
            entry = self.documents.get(doc_id)
            if entry is None:
                continue
            version = int(row.get("version", 1) or 1)
            is_latest = version == entry.max_version
            targets = (
                [self.history, self.latest] if is_latest else [self.history]
            )

            template_id = row.get("template_id", "") or ""
            template_version = int(row.get("template_version", 1) or 1)
            tpl = self.templates.get(template_id)
            if tpl is not None:
                tpl.document_version_count += 1
                if is_latest:
                    tpl.docs_per_template_version[template_version] += 1
            elif template_id:
                self._record_external(
                    "template", template_id, EXTERNAL_UNKNOWN, f"document {doc_id}"
                )
            for acc in targets:
                acc.pins[(template_id, template_version)] += 1

            self._document_reference_edges(row, doc_id, template_id, targets)
            self._term_reference_edges(row, template_id, targets)
            self._file_reference_edges(row, doc_id, template_id, targets)

    def _document_reference_edges(
        self,
        row: dict[str, Any],
        doc_id: str,
        template_id: str,
        targets: list[ReferenceGraph],
    ) -> None:
        is_edge_type = (
            template_id in self.templates and self.templates[template_id].is_edge_type
        )
        for ref in row.get("references") or []:
            if ref.get("reference_type") != "document":
                continue
            resolved = ref.get("resolved") or {}
            target_doc = resolved.get("document_id")
            if not target_doc:
                continue
            target_entry = self.documents.get(target_doc)
            if target_entry is None:
                # A reference into a namespace this archive carries, pointing
                # at a document it does not, is broken rather than external —
                # the distinction the restore door will make on arrival.
                target_ns = resolved.get("namespace")
                if target_ns and target_ns in self.namespaces:
                    self.dangling.append(
                        ExternalRef(
                            "document",
                            target_doc,
                            "target-missing-from-archive",
                            f"document {doc_id}",
                            template_id,
                        )
                    )
                else:
                    self._record_external(
                        "document",
                        target_doc,
                        EXTERNAL_OTHER_NAMESPACE
                        if target_ns
                        else EXTERNAL_UNKNOWN,
                        f"document {doc_id}",
                        template_id,
                    )
                continue

            for acc in targets:
                acc.reference_edges[(template_id, target_entry.template_id)] += 1
                if is_edge_type:
                    role = str(ref.get("field_path") or "endpoint")
                    acc.relationship_endpoints[(template_id, role)].add(target_doc)
        if is_edge_type:
            for acc in targets:
                acc.relationship_docs[template_id] += 1

    def _term_reference_edges(
        self,
        row: dict[str, Any],
        template_id: str,
        targets: list[ReferenceGraph],
    ) -> None:
        for tref in row.get("term_references") or []:
            term_id = tref.get("term_id")
            if not term_id:
                continue
            term = self.terms.get(term_id)
            if term is None:
                self._record_external(
                    "term",
                    term_id,
                    EXTERNAL_UNKNOWN,
                    f"template {self.template_label(template_id)}",
                    template_id,
                )
                continue
            for acc in targets:
                acc.term_use[term_id] += 1
                acc.terminology_use[(template_id, term.terminology_id)] += 1

    def _file_reference_edges(
        self,
        row: dict[str, Any],
        doc_id: str,
        template_id: str,
        targets: list[ReferenceGraph],
    ) -> None:
        for fref in row.get("file_references") or []:
            file_id = fref.get("file_id")
            if not file_id:
                continue
            if file_id not in self.files:
                self._record_external(
                    "file",
                    file_id,
                    EXTERNAL_UNKNOWN,
                    f"document {doc_id}",
                    template_id,
                )
                continue
            for acc in targets:
                acc.file_use[file_id] += 1

    def _record_external(
        self,
        kind: str,
        target_id: str,
        reason: str,
        referenced_by: str,
        origin_template: str | None = None,
    ) -> None:
        self.external_refs.append(
            ExternalRef(kind, target_id, reason, referenced_by, origin_template)
        )

    def _classify_declared_references(self) -> None:
        """Label declared references whose target the archive does not carry.

        Nothing is followed: offline there is nothing to follow, and the
        restore door resolves these on the target instance exactly as a live
        cross-namespace reference resolves today.
        """
        for tpl in self.templates.values():
            for node in tpl.versions.values():
                origin = f"template {tpl.label} v{node.version}"
                for ref in node.unresolved_templates:
                    self._record_external(
                        "template", ref, EXTERNAL_UNKNOWN, origin, tpl.template_id
                    )
                for ref in node.unresolved_terminologies:
                    self._record_external(
                        "terminology", ref, EXTERNAL_UNKNOWN, origin, tpl.template_id
                    )

    # -------------------------------------------------------------- labels

    def template_label(self, template_id: str) -> str:
        tpl = self.templates.get(template_id)
        return tpl.label if tpl else template_id

    def terminology_label(self, terminology_id: str) -> str:
        term = self.terminologies.get(terminology_id)
        return term.label if term else terminology_id

    def resolve_template(self, name: str) -> TemplateNode | None:
        """Find a template by value or id — the CLI accepts either."""
        if name in self.templates:
            return self.templates[name]
        for tpl in self.templates.values():
            if tpl.value == name:
                return tpl
        return None

    def resolve_terminology(self, name: str) -> TerminologyNode | None:
        if name in self.terminologies:
            return self.terminologies[name]
        for node in self.terminologies.values():
            if node.value == name:
                return node
        return None

    # ------------------------------------------------------------ analyses

    def declared_out_neighbours(self, template_id: str) -> set[str]:
        """Templates this one declares a dependency on, across its versions."""
        tpl = self.templates.get(template_id)
        if tpl is None:
            return set()
        out: set[str] = set()
        for node in tpl.versions.values():
            out |= node.all_declared_templates()
        return {t for t in out if t != template_id}

    def declared_terminologies(self, template_id: str) -> set[str]:
        tpl = self.templates.get(template_id)
        if tpl is None:
            return set()
        out: set[str] = set()
        for node in tpl.versions.values():
            out |= node.declared_terminologies
        return out

    def declared_in_neighbours(self, template_id: str) -> set[str]:
        return {
            other
            for other in self.templates
            if other != template_id
            and template_id in self.declared_out_neighbours(other)
        }

    def schema_closure(self, template_ids: set[str]) -> ClosureResult:
        """Everything the *schema* of a selection needs to be self-contained."""
        seen: set[str] = set()
        queue = list(template_ids)
        terminologies: set[str] = set()
        external: list[ExternalRef] = []
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            tpl = self.templates.get(current)
            if tpl is None:
                external.append(
                    ExternalRef("template", current, EXTERNAL_UNKNOWN, "closure")
                )
                continue
            for node in tpl.versions.values():
                # Declared references were resolved to ids at load; whatever
                # stayed unresolved is not in this archive, so it travels as
                # an external dependency rather than as a closure member.
                queue.extend(node.all_declared_templates())
                terminologies |= node.declared_terminologies
                external.extend(
                    ExternalRef(
                        "template", ref, EXTERNAL_UNKNOWN, f"template {tpl.label}"
                    )
                    for ref in node.unresolved_templates
                )
                external.extend(
                    ExternalRef(
                        "terminology", ref, EXTERNAL_UNKNOWN, f"template {tpl.label}"
                    )
                    for ref in node.unresolved_terminologies
                )
        return ClosureResult(seen - template_ids, terminologies, external)

    def data_closure(
        self, template_ids: set[str], *, latest_only: bool = False
    ) -> ClosureResult:
        """Everything a selection's *documents* actually reach.

        Follows real reference snapshots rather than declared targets, so it
        answers "what would break if I took only this" — a template may
        declare a reference its documents never use, and may hold references
        the schema no longer declares.
        """
        acc = self.latest if latest_only else self.history
        seen: set[str] = set()
        queue = list(template_ids)
        terminologies: set[str] = set()
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            for target in acc.out_neighbours(current):
                if target and target not in seen:
                    queue.append(target)
            terminologies |= acc.terminologies_of(current)
        reached = seen | template_ids
        external = [
            e
            for e in self.external_refs
            if e.kind in ("document", "term", "file")
            and e.origin_template in reached
        ]
        return ClosureResult(seen - template_ids, terminologies, external)

    def islands(self) -> list[Island]:
        """Connected components of templates, with terminologies attached."""
        if self._islands is not None:
            return self._islands

        adjacency: dict[str, set[str]] = {t: set() for t in self.templates}
        for template_id in self.templates:
            for other in self.declared_out_neighbours(template_id):
                if other in adjacency:
                    adjacency[template_id].add(other)
                    adjacency[other].add(template_id)
        for (src, dst) in self.history.reference_edges:
            if src in adjacency and dst in adjacency and src != dst:
                adjacency[src].add(dst)
                adjacency[dst].add(src)

        islands: list[Island] = []
        unvisited = set(adjacency)
        while unvisited:
            root = min(unvisited)
            component: set[str] = set()
            stack = [root]
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                unvisited.discard(current)
                stack.extend(adjacency[current] - component)
            terminologies: set[str] = set()
            for template_id in component:
                terminologies |= {
                    t
                    for t in self.declared_terminologies(template_id)
                    if t in self.terminologies
                }
                terminologies |= {
                    terminology_id
                    for terminology_id in self.history.terminologies_of(template_id)
                    if terminology_id in self.terminologies
                }
            islands.append(Island(len(islands), component, terminologies))

        islands.sort(key=lambda i: (-i.size, sorted(i.template_ids)[0]))
        for position, island in enumerate(islands):
            island.index = position
            for template_id in island.template_ids:
                self._island_of[template_id] = position
        self._islands = islands
        return islands

    def shared_terminologies(self) -> dict[str, list[int]]:
        """Terminologies used by more than one island.

        Extracting those islands separately duplicates the vocabulary into
        each archive; a later merge-on-return reconciles the copies by
        identity. Flagging them is what makes that cost visible up front.
        """
        by_terminology: dict[str, list[int]] = defaultdict(list)
        for island in self.islands():
            for terminology_id in island.terminology_ids:
                by_terminology[terminology_id].append(island.index)
        return {t: i for t, i in by_terminology.items() if len(i) > 1}

    def island_of(self, template_id: str) -> int | None:
        self.islands()
        return self._island_of.get(template_id)

    def template_report(self, template_id: str) -> TemplateReport | None:
        tpl = self.templates.get(template_id)
        if tpl is None:
            return None
        incoming_declared = self.declared_in_neighbours(template_id)
        incoming_actual = {
            src for src in self.history.in_neighbours(template_id) if src
        }
        return TemplateReport(
            template_id=template_id,
            label=tpl.label,
            namespace=tpl.namespace,
            usage=tpl.usage,
            versioned=tpl.versioned,
            document_count=len(tpl.document_ids),
            document_version_count=tpl.document_version_count,
            docs_per_template_version=dict(
                sorted(tpl.docs_per_template_version.items())
            ),
            in_degree_declared=len(incoming_declared),
            in_degree_actual=len(incoming_actual - {template_id}),
            out_degree_declared=len(self.declared_out_neighbours(template_id)),
            out_degree_actual=len(
                self.history.out_neighbours(template_id) - {template_id}
            ),
            incoming_declared=sorted(
                self.template_label(t) for t in incoming_declared
            ),
            incoming_actual=sorted(
                self.template_label(t) for t in incoming_actual - {template_id}
            ),
            schema_closure=self.schema_closure({template_id}),
            data_closure=self.data_closure({template_id}),
            data_closure_latest_only=self.data_closure(
                {template_id}, latest_only=True
            ),
            island=self.island_of(template_id),
        )

    def edge_type_reports(self) -> list[EdgeTypeReport]:
        reports: list[EdgeTypeReport] = []
        for template_id, tpl in sorted(
            self.templates.items(), key=lambda kv: kv[1].label
        ):
            if not tpl.is_edge_type:
                continue
            latest = tpl.latest_version
            declared_sources = sorted(
                self.template_label(t)
                for t in (latest.endpoint_source_templates if latest else set())
            )
            declared_targets = sorted(
                self.template_label(t)
                for t in (latest.endpoint_target_templates if latest else set())
            )
            touched: dict[str, int] = {}
            for (edge_id, role), docs in self.history.relationship_endpoints.items():
                if edge_id == template_id:
                    touched[role] = len(docs)

            # What dropping this edge type would disconnect, per endpoint
            # template: the share of its documents that participate in at
            # least one relationship document here.
            participants: set[str] = set()
            for (edge_id, _role), docs in self.history.relationship_endpoints.items():
                if edge_id == template_id:
                    participants |= docs
            disconnection: list[dict[str, Any]] = []
            endpoint_templates = {
                self.documents[d].template_id
                for d in participants
                if d in self.documents
            }
            for endpoint_id in sorted(endpoint_templates):
                endpoint = self.templates.get(endpoint_id)
                if endpoint is None or not endpoint.document_ids:
                    continue
                connected = len(participants & endpoint.document_ids)
                total = len(endpoint.document_ids)
                disconnection.append(
                    {
                        "template": endpoint.label,
                        "connected_documents": connected,
                        "total_documents": total,
                        "percent": round(100.0 * connected / total, 1),
                    }
                )
            reports.append(
                EdgeTypeReport(
                    template_id=template_id,
                    label=tpl.label,
                    namespace=tpl.namespace,
                    versioned=tpl.versioned,
                    declared_source_templates=declared_sources,
                    declared_target_templates=declared_targets,
                    relationship_documents=self.history.relationship_docs.get(
                        template_id, 0
                    ),
                    relationship_documents_latest_only=(
                        self.latest.relationship_docs.get(template_id, 0)
                    ),
                    endpoints_touched=touched,
                    disconnection=disconnection,
                )
            )
        return reports

    def terminology_usage(self, terminology_id: str) -> dict[str, Any]:
        node = self.terminologies.get(terminology_id)
        if node is None:
            return {}
        used = {
            term_id
            for term_id in node.term_ids
            if self.history.term_use.get(term_id, 0) > 0
        }
        deprecated_used = used & node.deprecated_term_ids
        return {
            "terminology": node.label,
            "terminology_id": terminology_id,
            "namespace": node.namespace,
            "mutable": node.mutable,
            "terms": len(node.term_ids),
            "used": len(used),
            "unused": len(node.term_ids) - len(used),
            "deprecated": len(node.deprecated_term_ids),
            "deprecated_but_referenced": sorted(
                self.terms[t].value or t for t in deprecated_used
            ),
            "islands": sorted(
                island.index
                for island in self.islands()
                if terminology_id in island.terminology_ids
            ),
        }

    def terminology_terms(self, terminology_id: str) -> list[dict[str, Any]]:
        """Per-term rows for a vocabulary dive: usage, aliases, structure."""
        node = self.terminologies.get(terminology_id)
        if node is None:
            return []
        rows = []
        for term_id in node.term_ids:
            term = self.terms[term_id]
            rows.append(
                {
                    "term": term.value or term_id,
                    "term_id": term_id,
                    "status": term.status,
                    "references": self.history.term_use.get(term_id, 0),
                    "aliases": list(term.aliases),
                    "relations": term.relation_degree,
                }
            )
        rows.sort(key=lambda r: (-r["references"], r["term"]))
        return rows

    def identity_coverage(self) -> dict[str, Any]:
        """Registry-row coverage — the restore door's refusal condition.

        The door refuses an archive namespace that carries entity rows but no
        registry rows at all, because the restored namespace would fail every
        id-based read while looking healthy on list surfaces. Per-entity gaps
        below that threshold do not trip the door but describe the same class
        of damage, scoped to the entities that have no identity row.
        """
        per_namespace: dict[str, Any] = {}
        for ns in self.namespaces:
            counts = self.entity_counts.get(ns, {})
            entity_rows = sum(
                counts.get(et, 0) for et in IDENTITY_BEARING_ENTITY_TYPES
            )
            registry_rows = len(self.registry_ids.get(ns, set()))
            per_namespace[ns] = {
                "entity_rows": entity_rows,
                "registry_rows": registry_rows,
                "door_refuses": entity_rows > 0 and registry_rows == 0,
            }
        known = set().union(*self.registry_ids.values()) if self.registry_ids else set()
        missing: dict[str, list[str]] = {}
        for entity_type, ids in (
            ("templates", set(self.templates)),
            ("terminologies", set(self.terminologies)),
            ("terms", set(self.terms)),
            ("documents", set(self.documents)),
            ("files", set(self.files)),
        ):
            gap = sorted(ids - known)
            if gap:
                missing[entity_type] = gap
        return {"namespaces": per_namespace, "missing_registry_rows": missing}

    def count_verification(self) -> dict[str, Any]:
        """Manifest-declared row counts against the rows actually present.

        A manifest that disagrees with its own payload means the archive lost
        or gained rows after the counts were written — truncated upload,
        interrupted producer, hand-edited file. Nothing downstream re-derives
        the counts, so a silent disagreement stays silent until a restore
        comes up short.
        """
        declared = {entry.prefix: entry.counts for entry in self.manifest.namespaces}
        result: dict[str, Any] = {}
        for ns in self.namespaces:
            entry = declared.get(ns)
            rows = self.entity_counts.get(ns, {})
            mismatches: dict[str, dict[str, int]] = {}
            if entry is not None:
                for entity_type in ENTITY_FILES:
                    expected = getattr(entry, entity_type, 0)
                    actual = rows.get(entity_type, 0)
                    if expected != actual:
                        mismatches[entity_type] = {
                            "manifest": expected,
                            "actual": actual,
                        }
            result[ns] = {
                "declared": entry is not None,
                "mismatches": mismatches,
            }
        return result

    def blob_report(self) -> dict[str, Any]:
        referenced = {
            file_id
            for file_id in self.files
            if self.history.file_use.get(file_id, 0) > 0
        }
        orphan_metadata = sorted(set(self.files) - referenced)
        blobs_without_metadata = sorted(self.blob_ids - set(self.files))
        metadata_without_blob = (
            sorted(set(self.files) - self.blob_ids) if self.include_files else []
        )
        return {
            "file_entities": len(self.files),
            "blobs": len(self.blob_ids),
            "bytes": sum(f.size_bytes for f in self.files.values()),
            "referenced": len(referenced),
            "orphan_metadata": orphan_metadata,
            "blobs_without_metadata": blobs_without_metadata,
            "metadata_without_blob": metadata_without_blob,
        }

    def findings(self) -> list[Finding]:
        """Every analysis result that indicates a problem, most severe first."""
        out: list[Finding] = []

        if self.dangling:
            by_source: Counter = Counter(e.referenced_by for e in self.dangling)
            out.append(
                Finding(
                    severity="error",
                    finding_class="dangling_document_reference",
                    subject="documents",
                    detail=(
                        "reference snapshots point at documents in a namespace "
                        "this archive carries, but those documents are absent — "
                        "the references will not resolve after restore"
                    ),
                    count=len(self.dangling),
                    samples=tuple(sorted(by_source)[:5]),
                )
            )

        coverage = self.identity_coverage()
        refusing = [
            ns for ns, data in coverage["namespaces"].items() if data["door_refuses"]
        ]
        if refusing:
            out.append(
                Finding(
                    severity="error",
                    finding_class="missing_identity_rows",
                    subject=", ".join(sorted(refusing)),
                    detail=(
                        "namespace carries entity rows but no registry identity "
                        "rows — the restore door refuses this archive unless "
                        "allow_missing_identity is set, and a namespace restored "
                        "that way fails every id-based read"
                    ),
                    count=len(refusing),
                    samples=tuple(sorted(refusing)),
                )
            )
        for entity_type, ids in coverage["missing_registry_rows"].items():
            if entity_type in ("documents", "templates", "terminologies", "terms", "files"):
                out.append(
                    Finding(
                        severity="warning",
                        finding_class="entity_without_registry_row",
                        subject=entity_type,
                        detail=(
                            "entities carry no registry identity row, so they "
                            "will not resolve by id after restore"
                        ),
                        count=len(ids),
                        samples=tuple(ids[:5]),
                    )
                )

        for ns, verification in self.count_verification().items():
            if verification["mismatches"]:
                out.append(
                    Finding(
                        severity="warning",
                        finding_class="manifest_count_mismatch",
                        subject=ns,
                        detail=(
                            "the manifest's declared row counts disagree with "
                            "the rows present — the archive may be truncated "
                            "or was written by an interrupted producer"
                        ),
                        count=len(verification["mismatches"]),
                        samples=tuple(
                            f"{entity_type}: manifest {counts['manifest']}, "
                            f"actual {counts['actual']}"
                            for entity_type, counts in sorted(
                                verification["mismatches"].items()
                            )
                        ),
                    )
                )

        blobs = self.blob_report()
        if blobs["orphan_metadata"]:
            out.append(
                Finding(
                    severity="warning",
                    finding_class="orphan_blob",
                    subject="files",
                    detail="file entities that no document references",
                    count=len(blobs["orphan_metadata"]),
                    samples=tuple(blobs["orphan_metadata"][:5]),
                )
            )
        if blobs["metadata_without_blob"]:
            out.append(
                Finding(
                    severity="warning",
                    finding_class="missing_blob",
                    subject="files",
                    detail=(
                        "archive declares include_files but carries file "
                        "metadata whose binary content is absent"
                    ),
                    count=len(blobs["metadata_without_blob"]),
                    samples=tuple(blobs["metadata_without_blob"][:5]),
                )
            )

        if self.external_refs:
            by_kind: Counter = Counter(e.kind for e in self.external_refs)
            out.append(
                Finding(
                    severity="info",
                    finding_class="external_reference",
                    subject="archive",
                    detail=(
                        "references point outside this archive and are not "
                        "followed; the restore door resolves them on the target "
                        "instance, so they must exist there"
                    ),
                    count=len(self.external_refs),
                    samples=tuple(
                        f"{kind}:{count}" for kind, count in sorted(by_kind.items())
                    ),
                )
            )

        for terminology_id, node in self.terminologies.items():
            usage = self.terminology_usage(terminology_id)
            if usage.get("deprecated_but_referenced"):
                out.append(
                    Finding(
                        severity="warning",
                        finding_class="deprecated_term_referenced",
                        subject=node.label,
                        detail=(
                            "deprecated terms are still referenced by documents "
                            "in this archive"
                        ),
                        count=len(usage["deprecated_but_referenced"]),
                        samples=tuple(usage["deprecated_but_referenced"][:5]),
                    )
                )

        out.sort(key=lambda f: (f.rank, f.finding_class, f.subject))
        return out

    # ---------------------------------------------------------------- json

    def summary(self) -> dict[str, Any]:
        """Counts and manifest header — the top of every report."""
        return {
            "format_version": self.manifest.format_version,
            "tool_version": self.manifest.tool_version,
            "exported_at": str(self.manifest.exported_at),
            "source_host": self.manifest.source_host,
            "namespaces": list(self.namespaces),
            "include_files": self.manifest.include_files,
            "include_all_versions": self.manifest.include_all_versions,
            "templates": len(self.templates),
            "template_versions": sum(len(t.versions) for t in self.templates.values()),
            "terminologies": len(self.terminologies),
            "terms": len(self.terms),
            "term_relations": self.term_relation_count,
            "documents": len(self.documents),
            "document_versions": sum(
                d.version_count for d in self.documents.values()
            ),
            "files": len(self.files),
            "derived_from": self.manifest_raw.get("derived_from"),
        }

    def to_dict(
        self,
        *,
        templates: list[str] | None = None,
        terminologies: list[str] | None = None,
    ) -> dict[str, Any]:
        """The machine contract behind ``inspect --json``.

        Tables are for humans; this is the API other tooling builds on, so it
        leads with an explicit ``schema`` version and stays additive.
        """
        deep_templates = [
            report.to_dict(self)
            for report in (
                self.template_report(t) for t in (templates or [])
            )
            if report is not None
        ]
        return {
            "schema": SCHEMA_VERSION,
            "archive": self.summary(),
            "islands": [
                {
                    "index": island.index,
                    "templates": sorted(
                        self.template_label(t) for t in island.template_ids
                    ),
                    "terminologies": sorted(
                        self.terminology_label(t) for t in island.terminology_ids
                    ),
                }
                for island in self.islands()
            ],
            "shared_terminologies": {
                self.terminology_label(t): islands
                for t, islands in self.shared_terminologies().items()
            },
            "templates": [
                report.to_dict(self)
                for report in (
                    self.template_report(t) for t in sorted(self.templates)
                )
                if report is not None
            ],
            "terminologies": [
                self.terminology_usage(t) for t in sorted(self.terminologies)
            ],
            "edge_types": [r.to_dict() for r in self.edge_type_reports()],
            "files": self.blob_report(),
            "identity": self.identity_coverage(),
            "counts": self.count_verification(),
            "findings": [f.to_dict() for f in self.findings()],
            "deep_dive": {
                "templates": deep_templates,
                "terminologies": [
                    self.terminology_usage(node.terminology_id)
                    for node in (
                        self.resolve_terminology(name)
                        for name in (terminologies or [])
                    )
                    if node is not None
                ],
            },
        }
