#!/usr/bin/env python3
"""Layer-L backup/restore matrix runner (design doc §7).

A manual, on-demand live-stack test — NOT part of ``wip-test.sh`` or CI. Run by
the operator after backup/restore changes, pointed at a named deployment. It
provisions the §3 fixture into freshly minted namespaces, exercises a slice of
the §5 matrix cells across the §4 assertion planes, prints one table (cell x
planes x pass/fail x wall-time), and tears its namespaces down. Non-zero exit
on any failure — the table is the artifact to paste into a case or commit.

Built in slices. Slice 1: the runner skeleton, the X-02 counts-conservation
harness, the fresh-restore spine (R-05/R-13/R-15), the B-01/B-02 real-archive
count cells. Slice 2: restore-mode variety (R-01/R-08/X-01/X-05). Slice 3: the
cross-cutting sweeps — X-03 (leak harness, generalized over every surface),
X-04 (job-plane field ownership), X-06 (backup-of-a-restore) — plus B-03, the
instance-wide producer cell. Still to build: R-02/03/04/06/07/11/14/16 and the
failure-injection cells F-05/F-06.

Safety (design doc §7):
- Deployment-pointable: ``--install <name>`` or ``--base-url`` + ``--key-file``.
  The target is always stated and echoed in the report header.
- Namespace naming ``<HHMMSS>-00a/00b/00c/00e/00f`` from the run's start time,
  so a run's namespaces are unique-by-construction and recognizable at a glance.
- Writes ONLY inside its own minted namespaces (created ``deletion_mode: full``),
  never ``wip`` or anything pre-existing — which is what makes pointing it at a
  ``prod-test``-class deployment safe. Cleanup is a straight namespace delete.
  The one exception is B-03, which by its nature spans the instance; it is
  gated behind ``--allow-instance-wide`` and reported SKIPPED without it.
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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
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
    """One matrix cell's run: a title, the plane checks it made, timing.

    A cell can also be SKIPPED — a cell whose preconditions the invocation did
    not supply (B-03 without ``--allow-instance-wide``, R-03 without a second
    install). A skipped cell is reported in the table with its reason and does
    not fail the run; what it must never be is silently absent, which would
    read as coverage the run did not deliver.
    """

    cid: str
    title: str
    checks: list[Check] = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None
    wall_s: float = 0.0

    def __post_init__(self) -> None:
        global _CURRENT_CELL
        _CURRENT_CELL = self

    def check(self, plane: str, ok: bool, detail: str) -> bool:
        assert plane in PLANES, f"unknown plane {plane}"
        self.checks.append(Check(plane, bool(ok), detail))
        return bool(ok)

    def skip(self, reason: str) -> Cell:
        self.skipped = reason
        return self

    @property
    def ok(self) -> bool:
        if self.skipped is not None:
            return True
        return self.error is None and all(c.ok for c in self.checks)

    @property
    def planes_touched(self) -> list[str]:
        seen = [c.plane for c in self.checks]
        return [p for p in PLANES if p in seen]

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


# The Cell most recently constructed. A cell function builds its own Cell, so
# when one raises mid-way ``_wrap`` has no handle on it and would report an
# empty 0/0 — discarding assertions the run already paid for, which is as much
# a loss of evidence as never making them. This slot gives _wrap that handle.
# Safe because the runner is single-threaded with exactly one cell in flight.
_CURRENT_CELL: Cell | None = None


# --------------------------------------------------------------------------- #
# Job log (feeds X-04)
# --------------------------------------------------------------------------- #


@dataclass
class JobRun:
    """One backup/restore job this run drove, plus what it must look like.

    X-04 sweeps these at the end of the run rather than inline, because the
    job record is still being written after the job reports terminal: the
    validation back-link and the archive-lifecycle hook are detached writers
    that land on the record afterwards, and the field-ownership bugs this
    plane exists to catch are precisely a later writer erasing an earlier
    one's field. Asserting at terminal time would pass over the very race.
    """

    label: str
    kind: str                      # "backup" | "restore"
    expect_namespaces: list[str]   # the set the job must name as its real targets
    expect_options: dict[str, Any]  # option -> value the record must echo
    expect_result: bool            # does this job's terminal event carry details?
    expect_validation: bool        # does it trigger per-namespace validation jobs?
    snapshot: dict                 # the terminal record as first observed


# The runner is a single-run script with one client and no concurrency, so one
# module-level log is honest bookkeeping rather than hidden state — it saves
# threading a recorder parameter through every cell signature.
JOBS: list[JobRun] = []


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


def _backup_archive(
    client: WipClient,
    anchor_ns: str,
    *,
    also: list[str] | None = None,
    all_namespaces: bool = False,
    record: bool = True,
) -> tuple[dict, bytes]:
    """Server backup; returns the terminal job record and the archive bytes.

    ``record=False`` keeps the job out of the X-04 sweep — for a job the
    caller deletes on the way out, which the sweep could then not re-read.
    """
    body: dict[str, Any] = {"include_files": True}
    if all_namespaces:
        body["all_namespaces"] = True
    elif also:
        body["namespaces"] = also
    snap = client.post(
        f"/api/document-store/backup/namespaces/{anchor_ns}/backup", json_body=body
    )
    done = _poll_job(client, snap["job_id"])
    if done.get("status") != "complete":
        raise RuntimeError(f"backup of {anchor_ns} failed: {done.get('error')}")
    if record:
        JOBS.append(JobRun(
            label=f"backup {anchor_ns}" + (f" + {also}" if also else ""),
            kind="backup",
            expect_namespaces=[anchor_ns, *(also or [])],
            expect_options={"include_files": True, "all_namespaces": all_namespaces},
            # A backup's terminal event carries no details, so `result` stays
            # null by design — asserting it populated would fail a correct job.
            expect_result=False,
            expect_validation=False,
            snapshot=done,
        ))
    return done, _download_archive(
        client, snap["job_id"], expect_size=done.get("archive_size")
    )


def _download_archive(
    client: WipClient, job_id: str, *, expect_size: int | None, attempts: int = 3
) -> bytes:
    """Download a job's retained archive, retrying a truncated body.

    A download issued in the seconds after a backup completes can come back as
    200 + ``Content-Length: N`` + an EMPTY body. Observed once on prod-test:
    the same job downloaded whole a minute later, so the archive was never
    damaged — only the moment was wrong. The retry is deliberately loud: it
    keeps a three-minute run from dying on a transient without quietly
    absorbing a platform defect the runner exists to surface.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            data = client.get_bytes(
                f"/api/document-store/backup/jobs/{job_id}/download"
            )
            if expect_size is not None and len(data) != expect_size:
                last = f"got {len(data)} bytes, job says archive_size={expect_size}"
            elif not data.startswith(b"PK"):
                last = f"got {len(data)} bytes that are not a zip"
            else:
                if attempt > 1:
                    print(f"  ⚠ archive {job_id} downloaded on attempt {attempt}")
                return data
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
        print(f"  ⚠ archive {job_id} download attempt {attempt}/{attempts} "
              f"came back wrong ({last}) — retrying")
        time.sleep(3.0)
    raise RuntimeError(
        f"archive {job_id} would not download intact after {attempts} attempts: {last}"
    )


def _backup(client: WipClient, anchor_ns: str, *, also: list[str] | None = None) -> bytes:
    """Server backup of anchor_ns (+ optional extra namespaces); archive bytes."""
    return _backup_archive(client, anchor_ns, also=also)[1]


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
        # A synchronous refusal mints no job, so there is nothing for the
        # job-plane sweep to read — deliberately not recorded.
        return {"status": "refused", "http_status": exc.status, "error": exc.body[:300]}
    done = _poll_job(client, snap["job_id"])
    if done.get("status") != "complete":
        # A job that failed is a cell failure, reported by the cell that drove
        # it. X-04's subject is the bookkeeping of jobs that DID work — the
        # fields a failed job never reached are not a field-ownership finding.
        return done
    # Where the job must say it wrote. A fresh restore writes to its target,
    # NOT to the archived namespaces (CASE-745: the post-restore sync and
    # validation derive their scope from this field, so a wrong value means a
    # green verdict about the wrong namespace); restore/merge write each
    # archived namespace to itself, which for the runner's single-namespace
    # archives is the anchor.
    writes_to = [target_ns] if (mode == "fresh" and target_ns) else [url_ns]
    JOBS.append(JobRun(
        label=f"{mode}{' dry-run' if dry_run else ''} -> {','.join(writes_to)}",
        kind="restore",
        expect_namespaces=writes_to,
        expect_options={"mode": mode, "dry_run": dry_run},
        # Only the modes whose terminal event carries details populate
        # `result`: fresh and merge do, plus every dry run (the plan IS the
        # deliverable). The id-preserving restore emits a bare completion.
        expect_result=dry_run or mode in ("fresh", "merge"),
        # Every completed non-dry-run restore triggers one validation job per
        # written namespace; a dry run deliberately triggers none.
        expect_validation=not dry_run,
        snapshot=done,
    ))
    return done


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
    leak_findings: list[str], src_count_before: dict, src_count_after: dict,
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
    # PL-LEAK: the document slice of the X-03 harness sweep — no source
    # namespace name and no source identifier of any kind in the restored
    # documents. (X-03 asserts the same sweep across every other surface.)
    doc_leaks = [f for f in leak_findings if f.startswith("documents:")]
    c.check("PL-LEAK", not doc_leaks,
            f"no trace of source [{src_ns}] in the restored documents "
            f"({len(doc_leaks)} finding(s){': ' + '; '.join(doc_leaks[:5]) if doc_leaks else ''})")
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


def cell_x06_backup_of_a_restore(
    client: WipClient, builder: Any, *, first_ns: str, second_ns: str
) -> Cell:
    """X-06: back up a freshly restored namespace and restore THAT.

    Fidelity has to be transitive. Hop one is asserted against a fixture whose
    shape the runner built, so anything the restore quietly mangled into a
    self-consistent state survives that comparison; hop two re-derives the
    copy from the copy, where a mangled row has to either reproduce itself
    exactly or diverge visibly.
    """
    c = Cell("X-06", "backup-of-a-restore (transitive fidelity)")
    first_count = builder._count_namespace(first_ns)
    first_codes = _sample_codes(client, first_ns)
    tokens = _leak_tokens(client, first_ns)
    archive = _backup(client, first_ns)
    parsed = _parse_archive(archive)
    # The second-hop archive must itself be internally consistent — a first
    # restore that dropped rows would produce a smaller but still coherent
    # archive, which only the count comparison below catches.
    for ns, per_type in parsed.per_ns.items():
        for etype, (declared, streamed) in per_type.items():
            c.check("PL-JOB", declared == streamed,
                    f"[{ns}] {etype}: second-hop archive declares {declared}, streams {streamed}")
    job = _restore(client, archive, url_ns=second_ns, mode="fresh", target_ns=second_ns)
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"second-hop fresh restore completed "
                   f"(status={job.get('status')}, err={job.get('error')})"):
        return c
    second_count = builder._count_namespace(second_ns)
    diffs = {k: (first_count.get(k), second_count.get(k)) for k in CONSERVED_KEYS
             if first_count.get(k) != second_count.get(k)}
    c.check("PL-DATA", not diffs,
            f"second hop conserves the first copy's counts (diffs={diffs})")
    # Ids are re-minted on every fresh hop, so content is what carries: the
    # identity values must survive both hops unchanged.
    second_codes = _sample_codes(client, second_ns)
    c.check("PL-DATA", second_codes == first_codes,
            f"sample identity values survive both hops "
            f"({first_codes} -> {second_codes})")
    c.check("PL-REG",
            _value_form_resolves(client, second_ns, "terminologies", "terminology",
                                 "MATRIX_PRIORITY"),
            "value-form resolution survives the second hop too")
    findings, swept = leak_sweep(client, second_ns, tokens)
    c.check("PL-LEAK", not findings,
            f"no trace of the first copy [{first_ns}] in {second_ns} "
            f"(swept {sum(swept.values())} rows, {len(findings)} finding(s)"
            f"{': ' + '; '.join(findings[:5]) if findings else ''})")
    return c


def b03_partial_grant_refused(
    client: WipClient, target: Any, *, ns: str
) -> tuple[bool, str]:
    """B-03's permission half: is a key with admin on ONE namespace refused an
    instance-wide backup? Returns (refused, detail).

    Split out of the cell deliberately. This half is cheap and harmless — the
    refusal happens at the permission check, so no backup ever starts — while
    the cell's other half interrupts the whole instance (CASE-801). Keeping
    them separable means this assertion can be exercised against a live
    deployment on its own, which is how it was validated.

    The temporary key is revoked in a ``finally``: a stray admin-granted key
    outliving the run would be a worse leftover than any namespace.
    """
    key_name = f"matrix-partial-{time.strftime('%H%M%S')}"
    created = client.post(
        "/api/registry/api-keys",
        json_body={
            "name": key_name,
            "description": "backup-matrix runner B-03 partial-grant probe; revoked in-run",
            "namespaces": [ns],
            "grant_permission": "admin",
        },
    )
    try:
        partial = WipClient(replace(target, api_key=created["plaintext_key"]), timeout=60.0)
        try:
            # A new runtime key is live on the Registry (which owns the key
            # store) at once, but other services learn it from KeySyncService,
            # which polls the Registry every 30 s by default. Until that poll
            # lands the document-store answers 401 — and asserting the refusal
            # against an unrecognised key would "pass" while never reaching
            # the permission check it claims to test. So wait for the key to
            # be accepted somewhere it legitimately has admin, first.
            if not _await_key_live(partial, ns):
                return False, (
                    "the temporary key never became live on the document-store "
                    "(key sync polls the Registry every 30s) — the permission "
                    "check was never exercised"
                )
            partial.post(
                f"/api/document-store/backup/namespaces/{ns}/backup",
                json_body={"all_namespaces": True},
            )
            return False, "the partial-grant key's instance-wide backup was ACCEPTED"
        except ApiError as exc:
            if exc.status == 401:
                return False, "HTTP 401 — the key was not recognised, so the check did not run"
            # 404 rather than 403 is the convention: a namespace the caller has
            # no grant on must not have its existence confirmed.
            return exc.status in (403, 404), f"HTTP {exc.status}"
        finally:
            partial.close()
    finally:
        with contextlib.suppress(ApiError):
            client.delete(f"/api/registry/api-keys/{key_name}")


def _await_key_live(key_client: WipClient, ns: str, *, timeout_s: float = 45.0) -> bool:
    """Wait until the document-store recognises a freshly-minted runtime key."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            key_client.get(
                "/api/document-store/documents",
                params={"namespace": ns, "page_size": 1},
            )
            return True
        except ApiError as exc:
            if exc.status != 401:
                # Recognised, just not permitted here — good enough: the key
                # is live, which is all this wait is establishing.
                return True
        time.sleep(3.0)
    return False


def cell_b03_instance_wide(
    client: WipClient, target: Any, *, expect_present: list[str]
) -> Cell:
    """B-03: an ``all_namespaces`` backup spans every namespace including
    ``wip``, and a key without admin on all of them is refused.

    Both halves reach outside the runner's own namespaces, which is why the
    cell is gated: the backup READS every namespace on the instance, and the
    refusal half mints a temporary API key (a registry object, revoked in a
    finally). The archive job is deleted afterwards so an instance-sized
    archive is not left retained on the target.

    **This cell interrupts its target while it runs — see CASE-801.** Measured
    on prod-test: the instance-wide backup built an 853 MB archive on the
    document-store's event loop, so ``/health`` stopped answering within its
    5 s probe timeout, Kubernetes pulled the pod from the service endpoints,
    and every caller got 503 for about two and a half minutes. Run it against
    a deployment nobody is using. The gate is not paperwork.
    """
    c = Cell("B-03", "instance-wide backup + partial-grant refusal")
    refused, detail = b03_partial_grant_refused(client, target, ns=expect_present[0])
    c.check("PL-JOB", refused,
            f"partial-grant key (admin on {expect_present[0]} only) refused on "
            f"all_namespaces backup: {detail}")

    # Half 2 — the admin key's instance-wide archive really spans the instance.
    job, archive = _backup_archive(
        client, expect_present[0], all_namespaces=True, record=False
    )
    parsed = _parse_archive(archive)
    present = set(parsed.namespaces())
    c.check("PL-DATA", "wip" in present,
            f"the instance-wide archive includes the 'wip' namespace "
            f"({len(present)} namespaces in the manifest)")
    missing = [ns for ns in expect_present if ns not in present]
    c.check("PL-DATA", not missing,
            f"the runner's own namespaces are in the archive (missing={missing})")
    c.check("PL-JOB", sorted(job.get("namespaces") or []) == sorted(present),
            f"the job's namespaces match the archive's "
            f"({len(job.get('namespaces') or [])} vs {len(present)})")
    for ns, per_type in parsed.per_ns.items():
        for etype, (declared, streamed) in per_type.items():
            c.check("PL-JOB", declared == streamed,
                    f"[{ns}] {etype}: manifest declares {declared}, archive streams {streamed}")
    # An instance-sized archive is not something to leave lying on the target.
    with contextlib.suppress(ApiError):
        client.delete(f"/api/document-store/backup/jobs/{job['job_id']}")
    return c


def cell_x04_job_plane(client: WipClient) -> Cell:
    """X-04: sweep every job this run drove against a schema of field ownership.

    Re-reads each record now that all the detached writers have landed, and
    asserts three things per job: the record says where it actually wrote
    (CASE-745), it echoes the options it was given, and every field an owner
    wrote is still there. That last one is the CASE-747/749/750 family at the
    E2E layer — those bugs were a second writer replaying a stale full-document
    copy over a first writer's field, so they are only visible in a re-read
    after the race window, never in the terminal response.
    """
    c = Cell("X-04", "job-plane sweep (field ownership across every job)")
    if not c.check("PL-JOB", bool(JOBS), f"the run drove jobs to sweep ({len(JOBS)})"):
        return c
    for run in JOBS:
        tag = f"[{run.label}]"
        now = client.get(f"/api/document-store/backup/jobs/{run.snapshot['job_id']}")
        c.check("PL-JOB", now.get("status") == "complete",
                f"{tag} terminal status complete (got {now.get('status')})")
        if run.expect_namespaces:
            c.check("PL-JOB", now.get("namespaces") == run.expect_namespaces,
                    f"{tag} namespaces == real write targets "
                    f"{run.expect_namespaces} (got {now.get('namespaces')})")
        opts = now.get("options") or {}
        for key, want in run.expect_options.items():
            c.check("PL-JOB", opts.get(key) == want,
                    f"{tag} options.{key} == {want!r} (got {opts.get(key)!r})")
        c.check("PL-JOB", bool(now.get("created_by")),
                f"{tag} created_by recorded ({now.get('created_by')!r})")
        c.check("PL-JOB", (now.get("archive_size") or 0) > 0,
                f"{tag} archive_size populated ({now.get('archive_size')})")
        # `result` is not universal: only the kinds whose terminal event
        # carries details have one. Asserting it both ways keeps the sweep
        # honest — a result appearing where none is expected is as much a
        # signal as one going missing.
        has_result = bool(now.get("result"))
        c.check("PL-JOB", has_result == run.expect_result,
                f"{tag} result populated={has_result}, expected {run.expect_result}")
        if not run.expect_validation:
            continue
        # PL-AUTO: the back-link survived (it is written by a detached task
        # ~ms after the job goes terminal, concurrently with the archive
        # lifecycle hook), and each validation it names actually ran against
        # a namespace this restore wrote — not the source it read.
        val_ids = now.get("validation_job_ids") or []
        if not c.check("PL-AUTO", bool(val_ids),
                       f"{tag} validation_job_ids survived "
                       f"(got {val_ids}, snapshot had "
                       f"{run.snapshot.get('validation_job_ids')})"):
            continue
        for vid in val_ids:
            try:
                vjob = _poll_job(client, vid, timeout_s=45.0)
            except (ApiError, TimeoutError) as exc:
                c.check("PL-AUTO", False, f"{tag} validation job {vid} did not settle: {exc}")
                continue
            c.check("PL-AUTO", vjob.get("namespace") in run.expect_namespaces,
                    f"{tag} validation {vid} scoped to a written namespace "
                    f"(namespace={vjob.get('namespace')}, written={run.expect_namespaces})")
            c.check("PL-AUTO", vjob.get("status") == "complete",
                    f"{tag} validation {vid} completed (status={vjob.get('status')})")
            # The findings must be ON the record. A validation whose result
            # was nulled reports nothing while looking like it ran — the
            # exact shape the field-scoped writes were introduced to stop.
            # The verdict itself is reported, not gated: a restored namespace
            # holding a reference into a namespace the run has since dropped
            # is legitimately unhealthy, and this cell is about the job
            # plane, not the data.
            vres = vjob.get("result") or {}
            c.check("PL-AUTO", vres.get("result_kind") == "namespace_integrity",
                    f"{tag} validation {vid} carries its findings "
                    f"(result_kind={vres.get('result_kind')!r}, "
                    f"verdict={vres.get('status')!r})")
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


def _sample_codes(client: WipClient, ns: str) -> list[str]:
    """The MATRIX_SAMPLE identity values — content, not identifiers.

    Ids are re-minted by a fresh restore, so they cannot say whether the
    payload survived; the identity field can.
    """
    return sorted(
        d.get("data", {}).get("sample_code", "")
        for d in _list_docs(client, ns, "MATRIX_SAMPLE")
    )


# --------------------------------------------------------------------------- #
# X-03: the leak-sweep harness
# --------------------------------------------------------------------------- #

# Cap on per-entry registry detail fetches. Registry detail is one GET per
# entry, so a large namespace would dominate the run's wall time. If a sweep
# ever hits this it says so in the check text — a silently truncated sweep
# would read as "no leaks found" when it means "not all rows were looked at".
_REGISTRY_DETAIL_CAP = 250


def _registry_entries(client: WipClient, ns: str) -> list[dict]:
    """Every registry entry in a namespace (browse listing, paged)."""
    out: list[dict] = []
    page = 1
    while True:
        resp = client.get(
            "/api/registry/entries",
            params={"namespace": ns, "page": page, "page_size": 100},
        )
        out.extend(resp.get("items", []))
        if page >= resp.get("pages", 1) or not resp.get("items"):
            break
        page += 1
    return out


def _surfaces(client: WipClient, ns: str) -> dict[str, list[dict]]:
    """Every serialized row surface a restore writes into a namespace.

    The namespace RECORD is deliberately absent: a fresh restore writes its
    provenance ("restored from an archive of '<source>'") onto the target
    namespace's description on purpose, so sweeping it would flag a
    documented feature as a leak. Everything below is entity data, where the
    source must not survive at all.
    """
    surfaces: dict[str, list[dict]] = {}
    surfaces["documents"] = _all_docs_serialized(client, ns)
    surfaces["templates"] = client.get(
        "/api/template-store/templates",
        params={"namespace": ns, "page_size": 1000},
    ).get("items", [])
    terminologies = client.get(
        "/api/def-store/terminologies",
        params={"namespace": ns, "page_size": 1000},
    ).get("items", [])
    surfaces["terminologies"] = terminologies
    terms: list[dict] = []
    for td in terminologies:
        terms.extend(client.get(
            f"/api/def-store/terminologies/{td['terminology_id']}/terms",
            params={"namespace": ns, "page_size": 1000},
        ).get("items", []))
    surfaces["terms"] = terms
    # Registry entries carry the richest leak surface — composite keys,
    # synonyms, search_values, source_info — and only the per-entry detail
    # endpoint returns them (the browse listing gives a synonym COUNT).
    details: list[dict] = []
    for entry in _registry_entries(client, ns)[:_REGISTRY_DETAIL_CAP]:
        with contextlib.suppress(ApiError):
            details.append(client.get(f"/api/registry/entries/{entry['entry_id']}"))
    surfaces["registry_entries"] = details
    return surfaces


def _leak_tokens(client: WipClient, ns: str) -> dict[str, str]:
    """Everything about a source namespace that must not appear in a copy.

    Token -> what it is, so a hit names the kind of leak, not just a string.
    """
    tokens: dict[str, str] = {ns: "source namespace name"}
    surfaces = _surfaces(client, ns)
    for doc in surfaces["documents"]:
        tokens[doc["document_id"]] = "source document id"
    for tpl in surfaces["templates"]:
        tokens[tpl["template_id"]] = "source template id"
    for td in surfaces["terminologies"]:
        tokens[td["terminology_id"]] = "source terminology id"
    for term in surfaces["terms"]:
        tokens[term["term_id"]] = "source term id"
    for entry in surfaces["registry_entries"]:
        tokens[entry["entry_id"]] = "source registry entry id"
    return {t: kind for t, kind in tokens.items() if t}


def leak_sweep(
    client: WipClient, target_ns: str, tokens: dict[str, str]
) -> tuple[list[str], dict[str, int]]:
    """Serialized rows x forbidden tokens. Returns (findings, rows swept).

    The harness the design doc asks every fresh cell to reuse: a fresh restore
    severs the copy from its source, so no source id and no source namespace
    name may survive anywhere in the target's entity data.
    """
    findings: list[str] = []
    swept: dict[str, int] = {}
    for name, rows in _surfaces(client, target_ns).items():
        swept[name] = len(rows)
        for row in rows:
            blob = json.dumps(row, default=str, sort_keys=True)
            for token, kind in tokens.items():
                if token in blob:
                    findings.append(f"{name}: {kind} {token!r} in row {_row_label(row)}")
    return findings, swept


def _row_label(row: dict) -> str:
    """A short handle for a row, for naming it in a leak finding."""
    for key in ("document_id", "template_id", "terminology_id", "term_id", "entry_id"):
        if row.get(key):
            return str(row[key])
    return "<unidentified row>"


def cell_x03_leak_sweep(
    *, tgt_ns: str, src_ns: str, tokens: dict[str, str],
    findings: list[str], swept: dict[str, int],
) -> Cell:
    """X-03: the generalized leak sweep — every restore-written surface in the
    target, crossed with every identifier of the source it was minted from.

    Takes an already-run sweep rather than running its own: R-05 asserts the
    document slice of the same sweep, and fetching every surface twice would
    double the cell's wall time to re-derive an identical answer.
    """
    c = Cell("X-03", "leak sweep harness (every surface x every source token)")
    # Silence is not pass: a sweep over zero rows or zero tokens finds nothing
    # and proves nothing, so the sweep's own coverage is asserted first.
    total_rows = sum(swept.values())
    c.check("PL-LEAK", total_rows > 0 and len(tokens) > 1,
            f"sweep has material: {total_rows} rows across "
            f"{len(swept)} surfaces {swept} x {len(tokens)} source tokens")
    for surface, count in swept.items():
        c.check("PL-LEAK", count > 0, f"surface '{surface}' had rows to sweep ({count})")
    c.check("PL-LEAK", not findings,
            f"no trace of source [{src_ns}] in {tgt_ns} "
            f"({len(findings)} finding(s){': ' + '; '.join(findings[:5]) if findings else ''})")
    return c


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
    print(f"namespaces: {ns_tag}-00a / {ns_tag}-00b / restore-targets "
          f"{ns_tag}-00c, -00e, -00f")
    print("=" * 74)
    print(f"{'cell':<7}{'planes':<30}{'checks':<9}{'result':<8}{'assert':>7}")
    print("-" * 74)
    for c in cells:
        planes = " ".join(p.replace("PL-", "") for p in c.planes_touched) or "-"
        result = "SKIP" if c.skipped else ("PASS" if c.ok else "FAIL")
        nchecks = "-" if c.skipped else f"{sum(1 for x in c.checks if x.ok)}/{len(c.checks)}"
        print(f"{c.cid:<7}{planes:<30}{nchecks:<9}{result:<8}{c.wall_s:>6.1f}s")
    print("-" * 74)
    skipped = sum(1 for c in cells if c.skipped)
    failed = sum(1 for c in cells if not c.ok)
    passed = len(cells) - failed - skipped
    print(f"{len(cells)} cells, {passed} pass, {failed} fail, {skipped} skip   "
          f"(total wall {elapsed_s:.1f}s incl. backup/restore)")
    # Verbose: every check, pass and fail, so the operator can see the actual
    # numbers behind a green run. On failure the detail always prints, and a
    # skip always names why it was skipped — a silent skip reads as coverage.
    for c in cells:
        if c.skipped:
            print(f"\n— {c.cid} — {c.title}\n    SKIPPED: {c.skipped}")
            continue
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
    p.add_argument(
        "--allow-instance-wide", action="store_true",
        help=(
            "enable B-03, the only cell that reaches outside the runner's own "
            "namespaces: it backs up EVERY namespace on the target (a read, "
            "and the archive job is deleted afterwards) and mints a temporary "
            "API key to prove a partial-grant key is refused (revoked in the "
            "same run). WARNING: the instance-wide backup INTERRUPTS the "
            "target — it blocks the document-store's event loop long enough "
            "to fail its health probes, so callers see 503 while it runs "
            "(CASE-801). Use an install nobody else is using. Without this "
            "flag B-03 is reported SKIPPED, never silently dropped."
        ),
    )
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
    ns_f = f"{tag}-00f"  # second-hop target (X-06)
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
            # The forbidden-token list has to be built while NS-B is still
            # intact — slice 2 drops and re-restores it further down.
            src_tokens = _leak_tokens(client, ns_b)
            archive_b = _backup(client, ns_b)
            job = _restore(client, archive_b, url_ns=tgt, mode="fresh", target_ns=tgt)
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

                # One sweep feeds both PL-LEAK cells: R-05 asserts its document
                # slice, X-03 asserts every surface.
                print("[slice3] X-03 leak sweep ...")
                leaks, swept = leak_sweep(client, tgt, src_tokens)
                cells.append(_wrap("R-05", cell_r05_fresh_beside_original, client, tgt_ns=tgt, src_ns=ns_b,
                                   leak_findings=leaks, src_count_before=src_count_b_before,
                                   src_count_after=src_count_b_after,
                                   tgt_docs_present=len(_all_docs_serialized(client, tgt))))
                cells.append(_wrap("X-03", cell_x03_leak_sweep, tgt_ns=tgt, src_ns=ns_b,
                                   tokens=src_tokens, findings=leaks, swept=swept))

                # X-06 re-derives a copy from the copy. It runs before slice 2
                # touches NS-B, since its subject is the restored target.
                print("[slice3] X-06 backup-of-a-restore ...")
                cells.append(_wrap("X-06", cell_x06_backup_of_a_restore, client, builder,
                                   first_ns=tgt, second_ns=ns_f))

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

                # B-03 is the one cell that reaches beyond the run's own
                # namespaces, so it is opt-in — and reported either way.
                if args.allow_instance_wide:
                    print("[slice3] B-03 instance-wide backup ...")
                    cells.append(_wrap("B-03", cell_b03_instance_wide, client, target,
                                       expect_present=[ns_a, ns_b]))
                else:
                    cells.append(Cell("B-03", "instance-wide backup + partial-grant refusal")
                                 .skip("needs --allow-instance-wide: the cell backs up every "
                                       "namespace on the target, which interrupts it (CASE-801), "
                                       "and mints a temporary API key"))

                # X-04 sweeps every job the run drove, last and before
                # teardown: the detached writers it checks for land after a
                # job reports terminal, so an inline assert would miss them.
                print("[slice3] X-04 job-plane sweep ...")
                cells.append(_wrap("X-04", cell_x04_job_plane, client))
        finally:
            if not args.keep:
                print("\n[teardown] deleting runner namespaces ...")
                teardown(client, [tgt, ns_e, ns_f, ns_a, ns_b])
            else:
                print(f"\n[keep] namespaces preserved: {ns_a}, {ns_b}, {tgt}, {ns_e}, {ns_f}")

        ok = print_report(target.source, tag, cells,
                          elapsed_s=time.monotonic() - t0, verbose=args.verbose)
    return 0 if ok else 1


def _wrap(cid: str, fn, *args, **kwargs) -> Cell:
    """Time a cell function, stamping its id and keeping its work on a crash."""
    start = time.monotonic()
    try:
        cell = fn(*args, **kwargs)
    except Exception as exc:
        # Keep the partially-built cell when the crash happened inside it, so
        # the checks it managed to make are still reported alongside the error.
        cell = (
            _CURRENT_CELL
            if _CURRENT_CELL is not None and _CURRENT_CELL.cid == cid
            else Cell(cid, "errored")
        )
        cell.error = f"{type(exc).__name__}: {exc}"
    cell.wall_s = time.monotonic() - start
    return cell


if __name__ == "__main__":
    raise SystemExit(main())
