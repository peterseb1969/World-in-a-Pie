#!/usr/bin/env python3
"""Layer-L backup/restore matrix runner (design doc §7).

A manual, on-demand live-stack test — NOT part of ``wip-test.sh`` or CI. Run by
the operator after backup/restore changes, pointed at a named deployment. It
provisions the §3 fixture into freshly minted namespaces, exercises a slice of
the §5 matrix cells across the §4 assertion planes, prints one table (cell x
planes x pass/fail x wall-time), and tears its namespaces down. Non-zero exit
on any failure — the table is the artifact to paste into a case or commit.

This is the first slice: the runner skeleton + the X-02 counts-conservation
harness + the three fresh-restore spine cells (R-05/R-13/R-15) + the B-01/B-02
real-archive count cells. Later slices fill the remaining restore cells, the
failure-injection cells, and the X-01/X-03..X-06 sweeps.

Safety (design doc §7):
- Deployment-pointable: ``--install <name>`` or ``--base-url`` + ``--key-file``.
  The target is always stated and echoed in the report header.
- Namespace naming ``<HHMMSS>-00a/00b/00c`` from the run's start time, so a
  run's namespaces are unique-by-construction and recognizable at a glance.
- Writes ONLY inside its own minted namespaces (created ``deletion_mode: full``),
  never ``wip`` or anything pre-existing — which is what makes pointing it at a
  ``prod-test``-class deployment safe. Cleanup is a straight namespace delete.
- ``--keep`` preserves namespaces for debugging; ``--cleanup-only`` sweeps
  ``??????-00*`` namespaces left by earlier crashed/kept runs.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fixtures import FixtureBuilder
from wip_http import ApiError, TargetError, WipClient, resolve_target

DEFAULT_ONTOLOGY = Path.home() / "Downloads" / "onto-test" / "goslim_generic.json"
RUNNER_BY = "backup-matrix-runner"

# The §4 assertion planes, in report order.
PLANES = ["PL-DATA", "PL-REG", "PL-REP", "PL-JOB", "PL-AUTO", "PL-LEAK", "PL-FILE"]

# Archive JSONL member per EntityCounts field (wip_archive.archive.ENTITY_FILES).
ENTITY_FILES = {
    "terminologies": "terminologies.jsonl",
    "terms": "terms.jsonl",
    "term_relations": "term_relations.jsonl",
    "templates": "templates.jsonl",
    "documents": "documents.jsonl",
    "files": "files.jsonl",
    "registry_entries": "registry_entries.jsonl",
}


# --------------------------------------------------------------------------- #
# Plane / cell framework
# --------------------------------------------------------------------------- #


@dataclass
class Check:
    plane: str
    ok: bool
    detail: str


@dataclass
class Cell:
    """One matrix cell's run: a title, the plane checks it made, timing."""

    cid: str
    title: str
    checks: list[Check] = field(default_factory=list)
    error: str | None = None
    wall_s: float = 0.0

    def check(self, plane: str, ok: bool, detail: str) -> bool:
        assert plane in PLANES, f"unknown plane {plane}"
        self.checks.append(Check(plane, bool(ok), detail))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return self.error is None and all(c.ok for c in self.checks)

    @property
    def planes_touched(self) -> list[str]:
        seen = [c.plane for c in self.checks]
        return [p for p in PLANES if p in seen]

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


# --------------------------------------------------------------------------- #
# Archive helpers
# --------------------------------------------------------------------------- #


def _poll_job(client: WipClient, job_id: str, *, timeout_s: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/document-store/backup/jobs/{job_id}")
        if last.get("status") in ("complete", "failed"):
            return last
        time.sleep(1.5)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s (last={last})")


def _backup(client: WipClient, anchor_ns: str, *, also: list[str] | None = None) -> bytes:
    """Server backup of anchor_ns (+ optional extra namespaces); archive bytes."""
    body: dict[str, Any] = {"include_files": True}
    if also:
        body["namespaces"] = also
    snap = client.post(
        f"/api/document-store/backup/namespaces/{anchor_ns}/backup", json_body=body
    )
    done = _poll_job(client, snap["job_id"])
    if done.get("status") != "complete":
        raise RuntimeError(f"backup of {anchor_ns} failed: {done.get('error')}")
    return client.get_bytes(
        f"/api/document-store/backup/jobs/{snap['job_id']}/download"
    )


def _fresh_restore(client: WipClient, archive: bytes, *, target_ns: str) -> dict:
    snap = client.post(
        f"/api/document-store/backup/namespaces/{target_ns}/restore",
        files={"archive": (f"{target_ns}.zip", archive, "application/zip")},
        data={"mode": "fresh", "target_namespace": target_ns},
    )
    return _poll_job(client, snap["job_id"])


def _restore(
    client: WipClient,
    archive: bytes,
    *,
    url_ns: str,
    mode: str,
    target_ns: str | None = None,
    dry_run: bool = False,
    on_clash: str = "skip",
    add_missing: bool = False,
) -> dict:
    """Generalized restore over the three modes.

    Returns the terminal job on success, or a synthetic
    ``{status: "refused", http_status, error}`` when the route rejects the
    upload synchronously (a refusal is an outcome the cells assert on, not a
    crash). ``url_ns`` is the auth anchor in the path; ``restore``/``merge``
    write each archived namespace to itself unless ``target_ns`` redirects.
    """
    data: dict[str, str] = {"mode": mode}
    if dry_run:
        data["dry_run"] = "true"
    if target_ns:
        data["target_namespace"] = target_ns
    if mode == "merge":
        data["on_clash"] = on_clash
        if add_missing:
            data["add_missing"] = "true"
    try:
        snap = client.post(
            f"/api/document-store/backup/namespaces/{url_ns}/restore",
            files={"archive": (f"{url_ns}.zip", archive, "application/zip")},
            data=data,
        )
    except ApiError as exc:
        return {"status": "refused", "http_status": exc.status, "error": exc.body[:300]}
    return _poll_job(client, snap["job_id"])


def _drop_ns(client: WipClient, ns: str) -> None:
    with contextlib.suppress(ApiError):
        client.delete(
            f"/api/registry/namespaces/{ns}",
            params={"force": True, "deleted_by": RUNNER_BY},
        )


@dataclass
class ParsedArchive:
    manifest: dict
    # per-namespace: {entity_type: (declared_count, streamed_count)}
    per_ns: dict[str, dict[str, tuple[int, int]]]
    blob_count: int

    def namespaces(self) -> list[str]:
        return list(self.per_ns)


def _parse_archive(archive: bytes) -> ParsedArchive:
    """Read the manifest and cross-check declared vs streamed record counts."""
    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in names else {}
        entries = manifest.get("namespaces") or []
        # Fall back to single-namespace shape if the v3 list is absent.
        if not entries and manifest.get("namespace"):
            entries = [{"prefix": manifest["namespace"], "counts": manifest.get("counts", {})}]
        per_ns: dict[str, dict[str, tuple[int, int]]] = {}
        for entry in entries:
            ns = entry["prefix"]
            declared = entry.get("counts", {})
            per_type: dict[str, tuple[int, int]] = {}
            for etype, fname in ENTITY_FILES.items():
                member = f"namespaces/{ns}/{fname}"
                streamed = 0
                if member in names:
                    with zf.open(member) as fh:
                        streamed = sum(1 for line in fh if line.strip())
                per_type[etype] = (int(declared.get(etype, 0)), streamed)
            per_ns[ns] = per_type
        blob_count = sum(1 for n in names if n.startswith("blobs/") and not n.endswith("/"))
    return ParsedArchive(manifest=manifest, per_ns=per_ns, blob_count=blob_count)


# --------------------------------------------------------------------------- #
# Count-comparison helpers
# --------------------------------------------------------------------------- #

# Archive EntityCounts field -> the fixture count() key it must equal. The
# archive is a FULL COPY (CASE-768): every version, every status. So templates
# map to all-versions and documents to the all-versions/all-status total.
ARCHIVE_TO_FIXTURE = {
    "terminologies": "terminologies",
    "term_relations": "term_relations",
    "templates": "templates_all_versions",
    "documents": "document_versions_total",
    "files": "files",
    "registry_entries": "registry_entries",
}

# Keys conserved across a fresh restore (ids are re-minted, counts are not).
CONSERVED_KEYS = [
    "terminologies", "terms_active", "term_relations",
    "templates_active_versions", "templates_all_versions", "edge_types",
    "documents_active_latest", "documents_archived_latest",
    "document_versions_total", "files", "registry_entries",
]


# --------------------------------------------------------------------------- #
# Cells
# --------------------------------------------------------------------------- #


def cell_backup_counts(
    cid: str, title: str, parsed: ParsedArchive, expected: dict[str, dict[str, Any]]
) -> Cell:
    """B-01 / B-02: manifest counts are internally consistent (declared ==
    streamed — the B-09 partial-damage cross-check) AND match the fixture's
    measured EXPECTED_COUNTS for every cleanly-mapped class."""
    c = Cell(cid, title)
    for ns, per_type in parsed.per_ns.items():
        exp = expected.get(ns)
        c.check("PL-JOB", exp is not None, f"[{ns}] namespace present in archive manifest")
        if exp is None:
            continue
        for etype, (declared, streamed) in per_type.items():
            # B-09 cross-check: the manifest cannot claim a count the archive
            # does not actually carry (a silently truncated member).
            c.check(
                "PL-JOB",
                declared == streamed,
                f"[{ns}] {etype}: manifest declares {declared}, archive streams {streamed}",
            )
            fkey = ARCHIVE_TO_FIXTURE.get(etype)
            if fkey is not None:
                want = exp[fkey]
                c.check(
                    "PL-DATA",
                    declared == want,
                    f"[{ns}] {etype}={declared} vs EXPECTED {fkey}={want}",
                )
        # terms: the archive carries every status; the fixture measures active
        # only, so the archive count is a lower-bounded superset (the deprecated
        # E12 term inflates it). Assert the floor rather than equality.
        terms_declared = per_type["terms"][0]
        c.check(
            "PL-DATA",
            terms_declared >= exp["terms_active"],
            f"[{ns}] terms={terms_declared} >= active EXPECTED {exp['terms_active']}",
        )
    return c


def cell_x02_conservation(
    source_count: dict[str, Any], target_count: dict[str, Any], *, src_ns: str, tgt_ns: str
) -> Cell:
    """X-02: a fresh restore conserves every counted class — the round-trip
    reproduces the source namespace's measured totals in the target."""
    c = Cell("X-02", "counts conservation (fresh restore round-trip)")
    for key in CONSERVED_KEYS:
        s, t = source_count.get(key), target_count.get(key)
        c.check("PL-DATA", s == t, f"{key}: source[{src_ns}]={s} -> target[{tgt_ns}]={t}")
    # registry_synonyms is reported for visibility but not a hard gate: a
    # re-mint can legitimately regenerate the auto-synonym set differently.
    s_syn, t_syn = source_count.get("registry_synonyms"), target_count.get("registry_synonyms")
    if s_syn != t_syn:
        c.check("PL-REG", True, f"(info) registry_synonyms {s_syn} -> {t_syn} (not gated)")
    return c


def cell_r15_prefixed_id_config(
    client: WipClient, *, tgt_ns: str, src_ns: str, src_sample_ids: list[str],
    tgt_sample_ids: list[str],
) -> Cell:
    """R-15: a prefixed-id_config namespace fresh-restored beside its live
    original gets its OWN id_config (UUID7, not the source prefix), re-mints
    without colliding, and leaks no source prefix into its ids."""
    c = Cell("R-15", "prefixed id_config through fresh restore")
    ns_cfg = client.get(f"/api/registry/namespaces/{tgt_ns}")
    doc_cfg = (ns_cfg.get("id_config") or {}).get("documents", {})
    algo = doc_cfg.get("algorithm")
    c.check("PL-REG", algo in (None, "uuid7"),
            f"target id_config.documents.algorithm={algo!r} (expected uuid7/default, not prefixed)")
    c.check("PL-REG", not doc_cfg.get("prefix"),
            f"target id_config carries no prefix (got {doc_cfg.get('prefix')!r})")
    leaked = [i for i in tgt_sample_ids if src_ns in i]
    c.check("PL-REG", not leaked,
            f"target sample ids carry no source prefix (leaked={leaked})")
    # No collision with the live source: the id sets are disjoint.
    overlap = set(src_sample_ids) & set(tgt_sample_ids)
    c.check("PL-REG", not overlap,
            f"target ids disjoint from the live source's (overlap={sorted(overlap)})")
    return c


def cell_r13_edge_through_restore(
    client: WipClient, *, tgt_ns: str, tgt_sample_ids: list[str], edges: list[dict]
) -> Cell:
    """R-13: an edge type restored fresh re-points source_ref/target_ref to the
    restored samples' new ids, and versioned:false overwrite-in-place still
    holds afterward (re-write keeps version 1, no fork)."""
    c = Cell("R-13", "edge type through fresh restore")
    if not c.check("PL-DATA", bool(edges), "a relationship document is present in the restore"):
        return c
    edge = edges[0]
    sref, tref = edge["data"].get("source_ref"), edge["data"].get("target_ref")
    c.check("PL-DATA", sref in tgt_sample_ids and tref in tgt_sample_ids,
            f"edge endpoints re-pointed to restored ids: source_ref={sref}, target_ref={tref}")
    # versioned:false overwrite-in-place: re-POST the same endpoints.
    try:
        client.post(
            "/api/document-store/documents",
            json_body=[{
                "template_id": "MATRIX_LINKED_TO",
                "namespace": tgt_ns,
                "data": {"source_ref": sref, "target_ref": tref},
            }],
        )
        after = _list_docs(client, tgt_ns, "MATRIX_LINKED_TO")
        edge2 = next((e for e in after if e["document_id"] == edge["document_id"]), None)
        v = edge2.get("version") if edge2 else None
        c.check("PL-DATA", len(after) == len(edges) and v == 1,
                f"overwrite-in-place: version={v}, edge count {len(edges)} -> {len(after)} (no fork)")
    except ApiError as exc:
        c.check("PL-DATA", False, f"overwrite-in-place errored: {exc}")
    return c


def cell_r05_fresh_beside_original(
    client: WipClient, *, tgt_ns: str, src_ns: str,
    src_doc_ids: list[str], src_count_before: dict, src_count_after: dict,
    tgt_docs_present: int,
) -> Cell:
    """R-05: fresh restore beside the live original — the copy lands, the
    original is untouched, and no restored row leaks a source id or namespace
    name (PL-LEAK)."""
    c = Cell("R-05", "fresh restore beside the live original")
    c.check("PL-DATA", tgt_docs_present > 0, f"copy landed: {tgt_docs_present} documents in {tgt_ns}")
    # Original untouched: every conserved count in the source is unchanged.
    unchanged = all(src_count_before.get(k) == src_count_after.get(k) for k in CONSERVED_KEYS)
    diffs = {k: (src_count_before.get(k), src_count_after.get(k))
             for k in CONSERVED_KEYS if src_count_before.get(k) != src_count_after.get(k)}
    c.check("PL-DATA", unchanged, f"source [{src_ns}] untouched by the restore (diffs={diffs})")
    # PL-LEAK: no source ns name or source doc id anywhere in the restored rows.
    blob = json.dumps(_all_docs_serialized(client, tgt_ns), default=str)
    ns_leak = f'"{src_ns}"' in blob or f"{src_ns}-D" in blob
    id_leaks = [i for i in src_doc_ids if i in blob]
    c.check("PL-LEAK", not ns_leak, f"no source namespace name/prefix in restored rows (ns_leak={ns_leak})")
    c.check("PL-LEAK", not id_leaks, f"no source document id in restored rows (leaks={id_leaks[:5]})")
    # PL-REG: a restored entity must resolve by its value-form, not only by its
    # canonical id — the guarantee Vision makes for every synonym. A fresh
    # restore currently drops the secondary {ns,type,value} lookup synonyms
    # (they are re-provisioned with the primary key only), so value-form
    # resolution of a restored terminology fails. This gate surfaces that gap.
    resolves = _value_form_resolves(client, tgt_ns, "terminologies", "terminology", "MATRIX_PRIORITY")
    c.check("PL-REG", resolves,
            "restored terminology MATRIX_PRIORITY resolves by value-form "
            "({ns,type,value} lookup synonym survives the fresh restore)")
    return c


def cell_x01_dry_run_parity(
    client: WipClient, builder: Any, archive: bytes, src_ns: str, target_ns: str
) -> Cell:
    """X-01: a fresh dry-run writes nothing, and the apply then produces exactly
    the source's conserved counts — the plan the dry-run implies == the outcome
    the apply delivers."""
    c = Cell("X-01", "dry-run parity (fresh restore)")
    src = builder._count_namespace(src_ns)
    dry = _restore(client, archive, url_ns=target_ns, mode="fresh",
                   target_ns=target_ns, dry_run=True)
    c.check("PL-JOB", dry.get("status") == "complete",
            f"dry-run completed (status={dry.get('status')})")
    c.check("PL-DATA", len(_all_docs_serialized(client, target_ns)) == 0,
            "dry-run wrote nothing into the target")
    job = _restore(client, archive, url_ns=target_ns, mode="fresh", target_ns=target_ns)
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"apply completed (status={job.get('status')})"):
        return c
    applied = builder._count_namespace(target_ns)
    diffs = {k: (src.get(k), applied.get(k)) for k in CONSERVED_KEYS
             if src.get(k) != applied.get(k)}
    c.check("PL-DATA", not diffs,
            f"apply produced the source's conserved counts (diffs={diffs})")
    return c


def cell_r01_id_preserving(
    client: WipClient, builder: Any, archive: bytes, ns: str
) -> Cell:
    """R-01: id-preserving restore into the emptied namespace (disaster
    recovery) — every id comes back verbatim, counts conserved, and value-form
    resolution intact (synonyms restored with the entries)."""
    c = Cell("R-01", "id-preserving restore into an emptied namespace")
    before_ids = sorted(d["document_id"] for d in _all_docs_serialized(client, ns))
    before = builder._count_namespace(ns)
    _drop_ns(client, ns)
    job = _restore(client, archive, url_ns=ns, mode="restore")
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"id-preserving restore completed "
                   f"(status={job.get('status')}, err={job.get('error')})"):
        return c
    after_ids = sorted(d["document_id"] for d in _all_docs_serialized(client, ns))
    c.check("PL-DATA", after_ids == before_ids,
            f"every document id preserved verbatim ({len(before_ids)} docs)")
    after = builder._count_namespace(ns)
    diffs = {k: (before.get(k), after.get(k)) for k in CONSERVED_KEYS
             if before.get(k) != after.get(k)}
    c.check("PL-DATA", not diffs,
            f"all conserved counts match the pre-drop namespace (diffs={diffs})")
    c.check("PL-REG",
            _value_form_resolves(client, ns, "terminologies", "terminology", "MATRIX_PRIORITY"),
            "restored terminology resolves by value-form (ids + synonyms preserved)")
    return c


def cell_x05_double_restore(client: WipClient, builder: Any, ns: str, archive: bytes) -> Cell:
    """X-05: a second id-preserving restore into the now-populated namespace is
    refused — the empty-target precondition makes restore non-idempotent by
    refusal, never by silent duplication."""
    c = Cell("X-05", "double-restore idempotence (id-preserving re-run refused)")
    before = len(_all_docs_serialized(client, ns))
    job = _restore(client, archive, url_ns=ns, mode="restore")
    c.check("PL-JOB", job.get("status") in ("refused", "failed"),
            f"second id-preserving restore into the non-empty namespace refused "
            f"(status={job.get('status')})")
    after = len(_all_docs_serialized(client, ns))
    c.check("PL-DATA", after == before, f"nothing duplicated ({before} -> {after} documents)")
    return c


def cell_r08_merge_drift(client: WipClient, archive: bytes, ns: str) -> Cell:
    """R-08: merge folds an archive into a drifted namespace — a document
    hard-deleted from the target is re-inserted by the merge, the rest left as
    the on_clash policy dictates."""
    c = Cell("R-08", "merge into a drifted namespace")
    samples = _list_docs(client, ns, "MATRIX_SAMPLE")
    if not c.check("PL-DATA", len(samples) >= 1, "target has samples to drift"):
        return c
    _hard_delete_doc(client, ns, samples[0]["document_id"])
    drifted = _list_docs(client, ns, "MATRIX_SAMPLE")
    c.check("PL-DATA", len(drifted) == len(samples) - 1,
            f"drift created — one sample removed ({len(samples)} -> {len(drifted)})")
    job = _restore(client, archive, url_ns=ns, mode="merge", on_clash="skip")
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"merge completed (status={job.get('status')}, err={job.get('error')})"):
        return c
    merged = _list_docs(client, ns, "MATRIX_SAMPLE")
    c.check("PL-DATA", len(merged) == len(samples),
            f"the drifted-away document was re-inserted by the merge "
            f"({len(drifted)} -> {len(merged)})")
    return c


# --------------------------------------------------------------------------- #
# Small query helpers
# --------------------------------------------------------------------------- #


def _hard_delete_doc(client: WipClient, ns: str, document_id: str) -> None:
    client.delete(
        "/api/document-store/documents",
        params={"namespace": ns},
        json_body=[{"id": document_id, "hard_delete": True}],
    )


def _list_docs(client: WipClient, ns: str, template_value: str) -> list[dict]:
    resp = client.get(
        "/api/document-store/documents",
        params={"namespace": ns, "template_value": template_value,
                "latest_only": True, "status": "active", "page_size": 100},
    )
    return resp.get("items", []) if isinstance(resp, dict) else []


def _all_docs_serialized(client: WipClient, ns: str) -> list[dict]:
    """Every document (all versions/statuses) in a namespace, for the leak sweep."""
    out: list[dict] = []
    page = 1
    while True:
        resp = client.post(
            "/api/document-store/documents/query",
            params={"namespace": ns},
            json_body={"status": None, "page": page, "page_size": 100},
        )
        items = resp.get("items", []) if isinstance(resp, dict) else []
        out.extend(items)
        pages = resp.get("pages", 1) if isinstance(resp, dict) else 1
        if page >= pages or not items:
            break
        page += 1
    return out


def _sample_ids(client: WipClient, ns: str) -> list[str]:
    return sorted(d["document_id"] for d in _list_docs(client, ns, "MATRIX_SAMPLE"))


def _value_form_resolves(
    client: WipClient, ns: str, entity_type: str, type_label: str, value: str
) -> bool:
    """Does the registry resolve this entity by its value-form key alone?

    The value-form key ``{ns, type, value}`` is a secondary lookup synonym
    (the primary composite key also carries ``label``). def-store/document-store
    auto-register it on original creation so an entity resolves by value without
    knowing its label — the resolution Vision requires to work identically to
    the canonical id.
    """
    resp = client.post(
        "/api/registry/entries/lookup/by-key",
        json_body=[{
            "namespace": ns,
            "entity_type": entity_type,
            "composite_key": {"ns": ns, "type": type_label, "value": value},
            "search_synonyms": True,
        }],
    )
    results = resp.get("results", []) if isinstance(resp, dict) else []
    return bool(results) and results[0].get("status") == "found"


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def print_report(
    target_src: str, ns_tag: str, cells: list[Cell], *, elapsed_s: float, verbose: bool
) -> bool:
    print("\n" + "=" * 74)
    print(f"layer-L matrix runner — target: {target_src}")
    print(f"namespaces: {ns_tag}-00a / {ns_tag}-00b / restore-target {ns_tag}-00c")
    print("=" * 74)
    print(f"{'cell':<7}{'planes':<30}{'checks':<9}{'result':<8}{'assert':>7}")
    print("-" * 74)
    for c in cells:
        planes = " ".join(p.replace("PL-", "") for p in c.planes_touched) or "-"
        result = "PASS" if c.ok else "FAIL"
        nchecks = f"{sum(1 for x in c.checks if x.ok)}/{len(c.checks)}"
        print(f"{c.cid:<7}{planes:<30}{nchecks:<9}{result:<8}{c.wall_s:>6.1f}s")
    print("-" * 74)
    passed = sum(1 for c in cells if c.ok)
    failed = len(cells) - passed
    print(f"{len(cells)} cells, {passed} pass, {failed} fail   "
          f"(total wall {elapsed_s:.1f}s incl. backup/restore)")
    # Verbose: every check, pass and fail, so the operator can see the actual
    # numbers behind a green run. On failure the detail always prints.
    for c in cells:
        if c.ok and not verbose:
            continue
        mark = "✓" if c.ok else "✗"
        print(f"\n{mark} {c.cid} — {c.title}")
        if c.error:
            print(f"    ERROR: {c.error}")
        shown = c.checks if verbose else c.failures()
        for x in shown:
            tick = "✓" if x.ok else "✗"
            print(f"    {tick} [{x.plane}] {x.detail}")
    return failed == 0


# --------------------------------------------------------------------------- #
# Cleanup
# --------------------------------------------------------------------------- #

_RUN_NS_RE = re.compile(r"^\d{6}-00[a-z]$")


def cleanup_only(client: WipClient) -> int:
    """Sweep ??????-00* namespaces left by earlier crashed/kept runs."""
    resp = client.get("/api/registry/namespaces", params={"page_size": 500})
    items = resp.get("items", []) if isinstance(resp, dict) else []
    victims = sorted(
        n["prefix"] for n in items
        if isinstance(n.get("prefix"), str) and _RUN_NS_RE.match(n["prefix"])
    )
    if not victims:
        print("cleanup: no matching ??????-00* namespaces found.")
        return 0
    print("cleanup: found these runner namespaces:")
    for v in victims:
        print(f"  - {v}")
    ans = input(f"delete all {len(victims)}? [y/N] ").strip().lower()
    if ans != "y":
        print("aborted.")
        return 1
    for v in victims:
        with contextlib.suppress(ApiError):
            client.delete(f"/api/registry/namespaces/{v}",
                          params={"force": True, "deleted_by": RUNNER_BY})
        print(f"  deleted {v}")
    return 0


def teardown(client: WipClient, namespaces: list[str]) -> None:
    for ns in namespaces:
        with contextlib.suppress(ApiError):
            client.delete(f"/api/registry/namespaces/{ns}",
                          params={"force": True, "deleted_by": RUNNER_BY})


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--install", help="wip-deploy install name")
    p.add_argument("--base-url")
    p.add_argument("--key-file")
    p.add_argument("--no-verify-tls", action="store_true")
    p.add_argument("--ontology", type=Path, default=DEFAULT_ONTOLOGY)
    p.add_argument("--keep", action="store_true", help="skip teardown")
    p.add_argument("--verbose", action="store_true",
                   help="print every plane check, not just failures")
    p.add_argument("--cleanup-only", action="store_true",
                   help="sweep leftover ??????-00* namespaces and exit")
    args = p.parse_args(argv)

    try:
        target = resolve_target(
            install=args.install, base_url=args.base_url,
            key_file=args.key_file, verify_tls=not args.no_verify_tls,
        )
    except TargetError as exc:
        print(f"target error: {exc}", file=sys.stderr)
        return 2

    if args.cleanup_only:
        with WipClient(target, timeout=60.0) as client:
            return cleanup_only(client)

    tag = time.strftime("%H%M%S")
    ns_a, ns_b, tgt = f"{tag}-00a", f"{tag}-00b", f"{tag}-00c"
    ns_e = f"{tag}-00e"  # dry-run-parity target (X-01)
    cells: list[Cell] = []
    t0 = time.monotonic()

    print(f"target: {target.source}")
    print(f"namespaces: {ns_a} / {ns_b} / restore-target {tgt}")

    with WipClient(target, timeout=120.0) as client:
        builder = FixtureBuilder(client, ns_a=ns_a, ns_b=ns_b, ontology_file=args.ontology)
        try:
            print("\n[provision] building fixture ...")
            report = builder.build()
            for w in report.warnings:
                print(f"  ⚠ {w}")

            expected = builder.count()["namespaces"]
            src_count_b_before = expected[ns_b]

            print("[B-01] backing up NS-A (single namespace) ...")
            arch_a = _parse_archive(_backup(client, ns_a))
            cells.append(_wrap("B-01", cell_backup_counts, "B-01",
                               "single-namespace real-archive counts (P-SRV1)", arch_a, expected))

            print("[B-02] backing up NS-A + NS-B (multi namespace) ...")
            arch_ab = _parse_archive(_backup(client, ns_a, also=[ns_b]))
            cells.append(_wrap("B-02", cell_backup_counts, "B-02",
                               "multi-namespace real-archive counts (P-SRVN)", arch_ab, expected))

            print("[restore] fresh-restoring NS-B beside the live original ...")
            src_sample_ids = _sample_ids(client, ns_b)
            src_doc_ids = [d["document_id"] for d in _all_docs_serialized(client, ns_b)]
            archive_b = _backup(client, ns_b)
            job = _fresh_restore(client, archive_b, target_ns=tgt)
            if job.get("status") != "complete":
                # The whole spine depends on a successful restore — record it
                # as a failed R-05 and skip the dependents.
                fail = Cell("R-05", "fresh restore beside the live original")
                fail.error = f"restore did not complete: status={job.get('status')} error={job.get('error')}"
                cells.append(fail)
            else:
                tgt_sample_ids = _sample_ids(client, tgt)
                tgt_edges = _list_docs(client, tgt, "MATRIX_LINKED_TO")
                tgt_count = builder._count_namespace(tgt)
                src_count_b_after = builder._count_namespace(ns_b)

                cells.append(_wrap("X-02", cell_x02_conservation, src_count_b_before, tgt_count,
                                   src_ns=ns_b, tgt_ns=tgt))
                cells.append(_wrap("R-15", cell_r15_prefixed_id_config, client, tgt_ns=tgt, src_ns=ns_b,
                                   src_sample_ids=src_sample_ids, tgt_sample_ids=tgt_sample_ids))
                cells.append(_wrap("R-13", cell_r13_edge_through_restore, client, tgt_ns=tgt,
                                   tgt_sample_ids=tgt_sample_ids, edges=tgt_edges))
                cells.append(_wrap("R-05", cell_r05_fresh_beside_original, client, tgt_ns=tgt, src_ns=ns_b,
                                   src_doc_ids=src_doc_ids, src_count_before=src_count_b_before,
                                   src_count_after=src_count_b_after,
                                   tgt_docs_present=len(_all_docs_serialized(client, tgt))))

                # Slice 2 — restore-mode variety. These reuse archive_b and NS-B.
                # X-01 first (reads NS-B while it is still intact); then R-01
                # empties + id-restores NS-B, X-05 re-restores it (refused), and
                # R-08 drifts + merges it. Ordered: each of R-01/X-05/R-08
                # depends on the prior NS-B state.
                print("[slice2] X-01 dry-run parity ...")
                cells.append(_wrap("X-01", cell_x01_dry_run_parity, client, builder,
                                   archive_b, ns_b, ns_e))
                print("[slice2] R-01 id-preserving restore (drops + restores NS-B) ...")
                cells.append(_wrap("R-01", cell_r01_id_preserving, client, builder, archive_b, ns_b))
                print("[slice2] X-05 double-restore idempotence ...")
                cells.append(_wrap("X-05", cell_x05_double_restore, client, builder, ns_b, archive_b))
                print("[slice2] R-08 merge into drift ...")
                cells.append(_wrap("R-08", cell_r08_merge_drift, client, archive_b, ns_b))
        finally:
            if not args.keep:
                print("\n[teardown] deleting runner namespaces ...")
                teardown(client, [tgt, ns_e, ns_a, ns_b])
            else:
                print(f"\n[keep] namespaces preserved: {ns_a}, {ns_b}, {tgt}")

        ok = print_report(target.source, tag, cells,
                          elapsed_s=time.monotonic() - t0, verbose=args.verbose)
    return 0 if ok else 1


def _wrap(cid: str, fn, *args, **kwargs) -> Cell:
    """Time a cell function and stamp its id onto any crash."""
    start = time.monotonic()
    try:
        cell = fn(*args, **kwargs)
    except Exception as exc:
        cell = Cell(cid, "errored")
        cell.error = f"{type(exc).__name__}: {exc}"
    cell.wall_s = time.monotonic() - start
    return cell


if __name__ == "__main__":
    raise SystemExit(main())
