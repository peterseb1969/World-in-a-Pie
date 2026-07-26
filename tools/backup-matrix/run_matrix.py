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
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    client: WipClient, anchor_ns: str, *, also: list[str] | None = None
) -> tuple[dict, bytes]:
    """Server backup; returns the terminal job record and the archive bytes.

    Downloads the whole archive, so it is for the runner's OWN namespaces only.
    The instance-wide cell uses `_start_instance_wide_backup` + a prefix read
    instead — see the note in cell_b03_instance_wide.
    """
    body: dict[str, Any] = {"include_files": True}
    if also:
        body["namespaces"] = also
    snap = client.post(
        f"/api/document-store/backup/namespaces/{anchor_ns}/backup", json_body=body
    )
    done = _poll_job(client, snap["job_id"])
    if done.get("status") != "complete":
        raise RuntimeError(f"backup of {anchor_ns} failed: {done.get('error')}")
    JOBS.append(JobRun(
        label=f"backup {anchor_ns}" + (f" + {also}" if also else ""),
        kind="backup",
        expect_namespaces=[anchor_ns, *(also or [])],
        expect_options={"include_files": True, "all_namespaces": False},
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


def _start_restore(
    client: WipClient,
    archive: bytes,
    *,
    url_ns: str,
    mode: str,
    target_ns: str | None = None,
    dry_run: bool = False,
    on_clash: str = "skip",
    add_missing: bool = False,
    skip_files: bool = False,
    namespace_map: dict[str, str] | None = None,
) -> dict:
    """POST the restore and return immediately — {job_id: …} or a refusal.

    The no-wait half of ``_restore``, split out so a cell can interrupt a
    run mid-flight (F-05). A synchronous refusal mints no job, so there is
    nothing for the job-plane sweep to read — deliberately not recorded.
    """
    data: dict[str, str] = {"mode": mode}
    if dry_run:
        data["dry_run"] = "true"
    if target_ns:
        data["target_namespace"] = target_ns
    if skip_files:
        data["skip_files"] = "true"
    if namespace_map:
        # Fresh only, and mandatory for a multi-namespace archive: there is no
        # implicit default, because an unmapped namespace restored to its old
        # name would collide with the live original.
        data["namespace_map"] = json.dumps(namespace_map)
    if mode == "merge":
        data["on_clash"] = on_clash
        if add_missing:
            data["add_missing"] = "true"
    try:
        return client.post(
            f"/api/document-store/backup/namespaces/{url_ns}/restore",
            files={"archive": (f"{url_ns}.zip", archive, "application/zip")},
            data=data,
        )
    except ApiError as exc:
        return {"status": "refused", "http_status": exc.status, "error": exc.body[:300]}


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
    skip_files: bool = False,
    expect_writes: list[str] | None = None,
    namespace_map: dict[str, str] | None = None,
) -> dict:
    """Generalized restore over the three modes.

    Returns the terminal job on success, or a synthetic
    ``{status: "refused", http_status, error}`` when the route rejects the
    upload synchronously (a refusal is an outcome the cells assert on, not a
    crash). ``url_ns`` is the auth anchor in the path; ``restore``/``merge``
    write each archived namespace to itself unless ``target_ns`` redirects.
    """
    snap = _start_restore(
        client, archive, url_ns=url_ns, mode=mode, target_ns=target_ns,
        dry_run=dry_run, on_clash=on_clash, add_missing=add_missing,
        skip_files=skip_files, namespace_map=namespace_map,
    )
    if "job_id" not in snap:
        return snap
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
    # …and a MULTI-namespace archive restored id-preservingly writes every
    # namespace it carries, which the anchor alone cannot express — R-02 is the
    # first cell to do that, so the caller declares it rather than this helper
    # guessing from a shape it can no longer infer.
    writes_to = (
        list(expect_writes) if expect_writes
        else [target_ns] if (mode == "fresh" and target_ns)
        else [url_ns]
    )
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


MANIFEST_PREFIX_BYTES = 512 * 1024


def _manifest_from_prefix(data: bytes) -> dict:
    """Parse ``manifest.json`` out of the first bytes of an archive.

    ArchiveWriter writes the manifest as the FIRST member of the zip, ahead of
    every entity file and blob, so it lands within the first few KB. That makes
    a prefix enough to read it — which is the only way to inspect an
    instance-wide archive without pulling the whole thing through the very
    service the run is trying to measure (CASE-803).

    Reads the local file header at fixed offsets (method, compressed size,
    name/extra lengths) and inflates that one member. The recorded sizes are
    trustworthy because the writer targets a real seekable file, so zipfile
    writes true sizes into the local header instead of deferring them to a
    trailing data descriptor.
    """
    if data[:4] != b"PK\x03\x04":
        raise ValueError("archive does not start with a zip local file header")
    (method,) = struct.unpack_from("<H", data, 8)
    (csize,) = struct.unpack_from("<I", data, 18)
    nlen, xlen = struct.unpack_from("<HH", data, 26)
    name = data[30:30 + nlen].decode("utf-8", "replace")
    if name != "manifest.json":
        raise ValueError(f"first archive member is {name!r}, expected manifest.json")
    start = 30 + nlen + xlen
    blob = data[start:start + csize]
    if len(blob) < csize:
        raise ValueError(
            f"prefix of {len(data)} bytes holds only {len(blob)} of the "
            f"manifest's {csize} compressed bytes — raise MANIFEST_PREFIX_BYTES"
        )
    raw = zlib.decompressobj(-15).decompress(blob) if method == 8 else blob
    return json.loads(raw)


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
    #
    # Only the MANIFEST is read, from a prefix of the download. The earlier
    # version pulled the entire archive — 853 MB on prod-test — to inspect one
    # small member, which pushed a gigabyte through the same service the run
    # was measuring and contaminated its own telemetry (CASE-803). The cost of
    # that is losing the per-entity declared-vs-streamed cross-check, which
    # cannot be done without inflating every member: B-01/B-02 already assert
    # it over the runner's own namespaces every run, and repeating it across
    # every namespace on the instance was never what made this cell distinct.
    job = _start_instance_wide_backup(client, expect_present[0])
    prefix = client.get_prefix(
        f"/api/document-store/backup/jobs/{job['job_id']}/download",
        max_bytes=MANIFEST_PREFIX_BYTES,
    )
    manifest = _manifest_from_prefix(prefix)
    entries = manifest.get("namespaces") or []
    present = {e["prefix"] for e in entries if e.get("prefix")}
    c.check("PL-DATA", "wip" in present,
            f"the instance-wide archive includes the 'wip' namespace "
            f"({len(present)} namespaces in the manifest, read from a "
            f"{len(prefix) // 1024} KB prefix of a {job.get('archive_size', 0) // 1024} KB archive)")
    missing = [ns for ns in expect_present if ns not in present]
    c.check("PL-DATA", not missing,
            f"the runner's own namespaces are in the archive (missing={missing})")
    c.check("PL-JOB", sorted(job.get("namespaces") or []) == sorted(present),
            f"the job's namespaces match the archive's manifest "
            f"({len(job.get('namespaces') or [])} vs {len(present)})")
    # An instance-sized archive is not something to leave lying on the target.
    with contextlib.suppress(ApiError):
        client.delete(f"/api/document-store/backup/jobs/{job['job_id']}")
    return c


def _start_instance_wide_backup(client: WipClient, anchor_ns: str) -> dict:
    """Run an all_namespaces backup to completion; return the terminal job.

    Separate from _backup_archive because this one must NOT download the
    archive — see the note in cell_b03_instance_wide.
    """
    snap = client.post(
        f"/api/document-store/backup/namespaces/{anchor_ns}/backup",
        json_body={"include_files": True, "all_namespaces": True},
    )
    done = _poll_job(client, snap["job_id"], timeout_s=900.0)
    if done.get("status") != "complete":
        raise RuntimeError(f"instance-wide backup failed: {done.get('error')}")
    return done


def cell_r14_blobs(
    client: WipClient, src_ns: str, tgt_ns: str, skip_tgt_ns: str,
) -> Cell:
    """R-14: E9 blobs survive a fresh restore, and skip_files drops them loudly.

    Files were the one entity class the matrix carried through backup counts
    but never through a restore: the merge suite uploads blobs for *inserted*
    files only, and the CLI round-trip excludes files entirely because it has
    no MinIO. So the bytes themselves — as opposed to the file *record* — had
    never made the trip.

    The byte-level compare is the point. A file row that restores with the
    right id and a missing or truncated blob passes every count assertion in
    the matrix; only reading the payload back distinguishes a restored file
    from a restored reference to nothing.
    """
    c = Cell("R-14", "blobs through a fresh restore; skip_files drops them loudly")

    src_files = client.get(
        "/api/document-store/files",
        params={"namespace": src_ns, "page_size": 100},
    ).get("items", [])
    if not c.check("PL-FILE", bool(src_files),
                   f"the fixture put file(s) in {src_ns} to carry ({len(src_files)}) "
                   "— file storage disabled on the target would make this vacuous"):
        return c

    src_blobs: dict[str, bytes] = {}
    for f in src_files:
        src_blobs[f["file_id"]] = client.get_bytes(
            f"/api/document-store/files/{f['file_id']}/content"
        )
    c.check("PL-FILE", all(src_blobs.values()),
            f"every source blob has bytes to compare ({len(src_blobs)})")

    archive = _backup(client, src_ns)
    parsed = _parse_archive(archive)
    c.check("PL-DATA", parsed.blob_count == len(src_files),
            f"the archive carries a blob per file "
            f"({parsed.blob_count} blobs / {len(src_files)} files)")

    job = _restore(client, archive, url_ns=tgt_ns, mode="fresh", target_ns=tgt_ns)
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"fresh restore with files completed (status={job.get('status')})"):
        return c

    tgt_files = client.get(
        "/api/document-store/files",
        params={"namespace": tgt_ns, "page_size": 100},
    ).get("items", [])
    c.check("PL-DATA", len(tgt_files) == len(src_files),
            f"file records conserved ({len(tgt_files)}/{len(src_files)})")

    # Ids are re-minted by a fresh restore, so match on content, not id.
    src_bytes = sorted(src_blobs.values())
    tgt_bytes = sorted(
        client.get_bytes(f"/api/document-store/files/{f['file_id']}/content")
        for f in tgt_files
    )
    c.check("PL-FILE", tgt_bytes == src_bytes,
            "every restored blob is byte-identical to its source "
            f"({len(tgt_bytes)} compared)")
    c.check("PL-REG", all(f["file_id"] not in src_blobs for f in tgt_files),
            "restored files carry NEW ids (fresh restore re-mints identity)")

    # skip_files: the records still land, the bytes deliberately do not.
    skip_job = _restore(client, archive, url_ns=skip_tgt_ns, mode="fresh",
                        target_ns=skip_tgt_ns, skip_files=True)
    if c.check("PL-JOB", skip_job.get("status") == "complete",
               f"skip_files restore completed (status={skip_job.get('status')})"):
        skipped_files = client.get(
            "/api/document-store/files",
            params={"namespace": skip_tgt_ns, "page_size": 100},
        ).get("items", [])
        c.check("PL-FILE", len(skipped_files) == 0,
                f"skip_files left no file records ({len(skipped_files)}) — "
                "loudly absent, not silently empty-bodied")
    return c


def cell_r16_reporting_parity(
    client: WipClient,
    tgt_ns: str,
    *,
    fts_ns: str,
    fts_query: str,
    fts_template: str,
) -> Cell:
    """R-16: the reporting layer is correct after a fresh restore — E13 + PL-REP.

    Reporting is `None` or stubbed in every merge and remap unit suite, so
    whether PostgreSQL reflects a restored namespace was simply unknown. A
    restore that leaves reporting empty or stale is invisible to the document
    API and breaks every SQL consumer downstream.

    Two halves, because parity and FTS fail independently. Parity compares row
    counts and table shape; it says nothing about whether the tsvector columns
    that full-text search actually matches on were built. CASE-810 is the proof
    that the difference matters: a type-filtered search returned zero hits on
    every post-split install while parity was perfectly happy, because search
    resolved `doc_<template>` as a BASE TABLE when post-split that name is a
    VIEW and the physical tables are `doc_<value>__v<N>`.

    The FTS half therefore searches for a document it knows was restored and
    demands a hit, and separately demands `unmatched_template is None`. That
    second assertion is the one that would have caught CASE-810 on its own:
    an empty result set cannot distinguish "this type has nothing" from "this
    filter matched no table", which is exactly why 810 survived unnoticed on
    two live instances until CASE-811 added the signal.
    """
    c = Cell("R-16", "reporting parity + FTS after a fresh restore (E13/PL-REP)")

    # Sync is event-driven and lands within seconds; poll rather than sleep a
    # fixed guess, and report the wait so a slow target is visible not silent.
    parity: dict = {}
    waited = 0.0
    for _ in range(30):
        try:
            parity = client.get(
                "/api/reporting-sync/parity",
                params={"namespace": tgt_ns, "include_counts": "true"},
            )
        except ApiError as exc:
            # Reporting-sync ships in the `standard`/`full` presets, not
            # `core`, and a rolling redeploy takes it out of the ingress for a
            # while. Either way the cell's precondition is absent, which is a
            # SKIP — reported in the table with its reason, never silently
            # dropped, and never counted as a pass it did not earn.
            return c.skip(
                f"reporting-sync unreachable ({exc.status}) — preset without "
                "reporting-sync, or the target is mid-redeploy"
            )
        if parity.get("ok"):
            break
        time.sleep(2.0)
        waited += 2.0

    c.check("PL-REP", bool(parity),
            f"parity endpoint answered for {tgt_ns} after {waited:.0f}s")
    c.check("PL-REP", bool(parity.get("schema_present")),
            f"the restored namespace has a PostgreSQL schema "
            f"({parity.get('schema_name')!r})")
    c.check("PL-REP", (parity.get("table_count") or 0) > 0,
            f"reporting built tables for the restored templates "
            f"(table_count={parity.get('table_count')})")
    c.check("PL-REP", parity.get("structural_issues") == 0,
            f"no structural issues (missing/mis-shaped tables): "
            f"{parity.get('structural_issues')}")
    c.check("PL-REP", parity.get("count_mismatches") == 0,
            f"row counts match Mongo for every synced template: "
            f"{parity.get('count_mismatches')} mismatch(es)")
    c.check("PL-REP", bool(parity.get("ok")),
            f"overall parity ok after {waited:.0f}s (ok={parity.get('ok')})")

    # --- E13: the FTS half -------------------------------------------------
    # Searched against the restored copy of NS-A, because that is where the
    # fixture's `full_text_indexed` field lives (MATRIX_SPECIMEN.description).
    # Sync is event-driven, so retry rather than assume it has landed.
    resp: dict = {}
    docs: dict = {}
    for _ in range(20):
        try:
            resp = client.post(
                "/api/reporting-sync/search",
                json_body={
                    "query": fts_query,
                    "types": ["document"],
                    "namespace": fts_ns,
                    "template": fts_template,
                },
            )
        except ApiError as exc:
            return c.skip(
                f"reporting-sync search unreachable ({exc.status}) — preset "
                "without reporting-sync, or the target is mid-redeploy"
            )
        docs = (resp.get("results") or {}).get("document") or {}
        if docs.get("total"):
            break
        time.sleep(2.0)

    # The CASE-810 guard, and the reason it is a separate assertion: a zero-hit
    # result is ambiguous on its own. This says the template filter resolved to
    # a real reporting table, so a zero above would mean "nothing matched"
    # rather than "the query never ran".
    c.check("PL-REP", resp.get("unmatched_template") is None,
            f"the template filter {fts_template!r} matched a reporting table "
            f"(unmatched_template={resp.get('unmatched_template')!r})")

    c.check("PL-REP", (docs.get("total") or 0) > 0,
            f"full-text search finds the restored document for {fts_query!r} "
            f"in {fts_ns} ({docs.get('total')} hit(s)) — tsvector columns were "
            "built on the restored per-version tables, which parity alone "
            "does not check")

    # A hit belonging to the SOURCE would mean the namespace filter leaked and
    # search answered from the original rather than the copy — invisible to the
    # count planes, which only ever look at one namespace at a time. Matched on
    # document id rather than a namespace field: SearchResult carries no
    # namespace, and asserting on a field that does not exist yields None and
    # a vacuous check.
    restored_ids = {
        d["document_id"] for d in _list_docs(client, fts_ns, fts_template)
    }
    hit_ids = {i.get("id") for i in (docs.get("items") or [])}
    c.check("PL-LEAK", bool(hit_ids) and hit_ids <= restored_ids,
            f"every FTS hit is a document of the restored namespace "
            f"({len(hit_ids)} hit id(s) against {len(restored_ids)} restored) — "
            f"a hit outside this set would be the source answering")
    return c


def cell_r11_restore_from_retained_job(client: WipClient, src_ns: str) -> Cell:
    """R-11: restore from a retained job's archive, without re-uploading.

    `POST /backup/jobs/{id}/restore` exists so an operator can re-run a
    restore from bytes the server already holds. The route promises the new
    job gets its OWN archive copy so deleting either never pulls the archive
    out from under the other — that independence is the half worth asserting,
    because a shared handle fails only later, when someone cleans up.

    Merge mode, not restore: `RestoreFromJobRequest` is strict and carries no
    `target_namespace`, so each archived namespace goes back to itself — and
    an id-preserving restore requires an EMPTY target, which the source
    namespace is not. `on_clash=skip` therefore exercises the retained-archive
    path without mutating the namespace the rest of the run still depends on.
    """
    c = Cell("R-11", "restore from a retained job; job independence (R-JOB)")

    snap, _ = _backup_archive(client, src_ns)
    src_job_id = snap["job_id"]
    c.check("PL-JOB", bool(src_job_id), f"source backup retained as {src_job_id}")

    try:
        new = client.post(
            f"/api/document-store/backup/jobs/{src_job_id}/restore",
            json_body={"mode": "merge", "on_clash": "skip"},
        )
    except ApiError as exc:
        c.check("PL-JOB", False,
                f"restore-from-job refused: {exc.status} {exc.body[:160]}")
        return c

    done = _poll_job(client, new["job_id"])
    if not c.check("PL-JOB", done.get("status") == "complete",
                   f"restore-from-job completed (status={done.get('status')} "
                   f"error={done.get('error')})"):
        return c
    c.check("PL-JOB", new["job_id"] != src_job_id,
            "the restore minted its own job rather than reusing the backup's")

    # Independence: dropping the SOURCE job must not disturb the restore's
    # own archive copy — the route's stated guarantee.
    client.delete(f"/api/document-store/backup/jobs/{src_job_id}")
    # This cell is the one that deletes a job on purpose, so it also owns
    # retracting it from the end-of-run sweep. X-04 re-reads every job in
    # JOBS and would 404 on this one — a failure that would say nothing about
    # field ownership and everything about this cell's side effect.
    JOBS[:] = [j for j in JOBS if j.snapshot.get("job_id") != src_job_id]
    after = client.get(f"/api/document-store/backup/jobs/{new['job_id']}")
    c.check("PL-JOB", after.get("status") == "complete",
            "the restore job survives deletion of the source backup job "
            f"(status={after.get('status')})")
    return c


def cell_r02_multi_ns_cross_refs(
    client: WipClient, archive_ab: bytes, ns_a: str, ns_b: str,
) -> Cell:
    """R-02: both namespaces restored together, cross-namespace refs intact.

    An id-preserving restore of a MULTI-namespace archive, which is the shape
    a real disaster recovery takes and the one no cell covered: R-01 restores
    a single namespace, and every other restore cell works on one at a time.

    What only this cell can show is that NS-A's references into NS-B still
    resolve afterwards. The fixture points `primary_sample` (scalar) and
    `linked_samples` (array) at NS-B documents, under a template-level
    `target_templates` pin, with NS-A configured `strict` and NS-B on its
    allow-list — so a restore that got the ordering or the id handling wrong
    leaves a document whose references name things that no longer exist.

    Counts cannot see this. A dangling reference is a perfectly well-formed
    string in a document whose class totals all reconcile; only resolving the
    referenced id against the other restored namespace distinguishes a live
    cross-namespace link from a plausible-looking corpse.
    """
    c = Cell("R-02", "multi-namespace id-preserving restore; cross-ns refs (E8) intact")

    # Evidence has to be gathered BEFORE the drop: after it there is nothing
    # to compare against, and the archive is the only remaining copy.
    specs_before = _list_docs(client, ns_a, "MATRIX_SPECIMEN")
    refs_before: dict[str, dict] = {}
    for d in specs_before:
        data = d.get("data") or {}
        linked = data.get("linked_samples") or []
        primary = data.get("primary_sample")
        if linked or primary:
            refs_before[d["document_id"]] = {
                "primary": primary,
                "linked": sorted(linked if isinstance(linked, list) else []),
            }
    if not c.check("PL-DATA", bool(refs_before),
                   f"NS-A holds specimen(s) with cross-namespace refs to carry "
                   f"({len(refs_before)})"):
        return c

    ids_b_before = {d["document_id"] for d in _list_docs(client, ns_b, "MATRIX_SAMPLE")}
    c.check("PL-DATA", bool(ids_b_before),
            f"NS-B holds the referenced sample documents ({len(ids_b_before)})")

    for ns in (ns_a, ns_b):
        _drop_ns(client, ns)

    job = _restore(client, archive_ab, url_ns=ns_a, mode="restore",
                   expect_writes=[ns_a, ns_b])
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"multi-namespace id-preserving restore completed "
                   f"(status={job.get('status')} error={job.get('error')})"):
        return c
    c.check("PL-JOB", sorted(job.get("namespaces") or []) == sorted([ns_a, ns_b]),
            f"the job names BOTH restored namespaces "
            f"(got {sorted(job.get('namespaces') or [])})")

    # Id-preserving: the same document ids must come back, on both sides.
    ids_b_after = {d["document_id"] for d in _list_docs(client, ns_b, "MATRIX_SAMPLE")}
    c.check("PL-REG", ids_b_after == ids_b_before,
            f"NS-B document ids preserved verbatim "
            f"({len(ids_b_after & ids_b_before)}/{len(ids_b_before)} matched)")

    specs_after = {d["document_id"]: d for d in _list_docs(client, ns_a, "MATRIX_SPECIMEN")}
    c.check("PL-REG", set(specs_after) >= set(refs_before),
            f"NS-A specimen ids preserved verbatim "
            f"({len(set(specs_after) & set(refs_before))}/{len(refs_before)})")

    # The cell's distinctive claim, in two independent halves.
    #
    # Cross-namespace references are stored in their QUALIFIED VALUE form —
    # '<ns>:<prefixed-id>', e.g. '…-00b:…-00b-D000001' — not as the referenced
    # document's UUID. So "does it resolve" cannot be set-membership against
    # document_ids; it has to be an actual resolution, which is the stronger
    # check anyway: it exercises the platform's rule that any valid synonym
    # behaves identically to the canonical id, across a namespace boundary,
    # after both namespaces were dropped and restored.
    unchanged = 0
    dangling: list[str] = []
    for doc_id, before in refs_before.items():
        after = (specs_after.get(doc_id) or {}).get("data") or {}
        linked_after = sorted(after.get("linked_samples") or [])
        primary_after = after.get("primary_sample")
        unchanged += bool(
            linked_after == before["linked"] and primary_after == before["primary"]
        )
        targets = set(linked_after) | ({primary_after} if primary_after else set())
        for ref in targets:
            # Stored qualified ('<ns>:<value>'); the document GET route wants
            # the value, so split on the FIRST colon per the platform's
            # qualified-name convention and keep any colons inside the value.
            lookup = ref.split(":", 1)[1] if ":" in ref else ref
            try:
                doc = client.get(f"/api/document-store/documents/{lookup}")
            except ApiError:
                dangling.append(ref)
                continue
            if doc.get("namespace") != ns_b:
                dangling.append(ref)

    c.check("PL-DATA", unchanged == len(refs_before),
            f"every cross-namespace ref survived the restore unchanged "
            f"({unchanged}/{len(refs_before)} specimen(s))")
    c.check("PL-REG", not dangling,
            f"every cross-namespace ref still RESOLVES to a document in the "
            f"restored {ns_b} ({len(dangling)} dangling: {dangling[:3]})")
    return c


def cell_r06_cross_source_refs(
    client: WipClient, archive_ab: bytes, ns_a: str, ns_b: str,
    tgt_a: str, tgt_b: str,
) -> Cell:
    """R-06: a multi-source FRESH restore rewrites cross-source refs both ways.

    R-02's sibling on the other restore mode. Where an id-preserving restore
    keeps every id — so a cross-namespace reference survives by simply not
    changing — a fresh restore re-mints every identity, which means each
    reference has to be actively rewritten to the copy's new id. Getting that
    wrong in the direction nobody tests leaves the copy silently pointing at
    the ORIGINAL, which is the worst outcome available: the restore looks
    complete, resolves fine, and quietly couples two namespaces that were
    supposed to be independent.

    So the assertions are about where the refs point, not merely that they do:
    every target id must be one of the COPY's ids, and none may be one of the
    source's.
    """
    c = Cell("R-06", "multi-source fresh restore; cross-source refs rewritten both ways")

    src_specs = _list_docs(client, ns_a, "MATRIX_SPECIMEN")
    src_sample_ids = {d["document_id"] for d in _list_docs(client, ns_b, "MATRIX_SAMPLE")}
    if not c.check("PL-DATA", bool(src_specs) and bool(src_sample_ids),
                   f"sources hold the cross-source pair to carry "
                   f"({len(src_specs)} specimen(s) -> {len(src_sample_ids)} sample(s))"):
        return c

    job = _restore(client, archive_ab, url_ns=tgt_a, mode="fresh",
                   namespace_map={ns_a: tgt_a, ns_b: tgt_b},
                   expect_writes=[tgt_a, tgt_b])
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"multi-source fresh restore completed "
                   f"(status={job.get('status')} error={job.get('error')})"):
        return c
    c.check("PL-JOB", sorted(job.get("namespaces") or []) == sorted([tgt_a, tgt_b]),
            f"the job names both TARGETS, not the sources "
            f"(got {sorted(job.get('namespaces') or [])})")

    copy_sample_ids = {d["document_id"] for d in _list_docs(client, tgt_b, "MATRIX_SAMPLE")}
    copy_specs = _list_docs(client, tgt_a, "MATRIX_SPECIMEN")
    c.check("PL-DATA", len(copy_specs) == len(src_specs),
            f"specimens conserved into {tgt_a} ({len(copy_specs)}/{len(src_specs)})")
    c.check("PL-REG", copy_sample_ids and not (copy_sample_ids & src_sample_ids),
            f"the copy's samples got NEW ids ({len(copy_sample_ids)}, "
            f"{len(copy_sample_ids & src_sample_ids)} shared with the source)")

    # Where do the copy's refs point? Resolve each and demand it lands in the
    # copied NS-B, never the original.
    to_copy = to_source = unresolved = 0
    for d in copy_specs:
        data = d.get("data") or {}
        refs = list(data.get("linked_samples") or [])
        if data.get("primary_sample"):
            refs.append(data["primary_sample"])
        for ref in refs:
            lookup = ref.split(":", 1)[1] if ":" in ref else ref
            try:
                doc = client.get(f"/api/document-store/documents/{lookup}")
            except ApiError:
                unresolved += 1
                continue
            ns = doc.get("namespace")
            to_copy += ns == tgt_b
            to_source += ns == ns_b

    c.check("PL-REG", unresolved == 0,
            f"every rewritten cross-source ref resolves ({unresolved} did not)")
    c.check("PL-LEAK", to_source == 0,
            f"no ref in the copy still points at the ORIGINAL {ns_b} "
            f"({to_source} did) — the failure that looks like success")
    c.check("PL-DATA", to_copy > 0,
            f"refs were rewritten onto the copied namespace {tgt_b} "
            f"({to_copy} ref(s))")
    return c


def cell_r07_collapse_refused(
    client: WipClient, archive_ab: bytes, ns_a: str, ns_b: str, tgt: str,
) -> Cell:
    """R-07: N:1 collapse — a same-valued terminology in both sources refuses.

    Several sources may share one target, and the fixture puts a terminology
    of the same VALUE in both namespaces. Collapsing them would mint two
    Registry entries claiming one composite key, so the plan must refuse
    rather than write half of it.

    Asserted on the DRY RUN as well as the apply, because a preview that
    permits what the apply refuses is worse than no preview: it is the run an
    operator trusts before doing the real thing.
    """
    c = Cell("R-07", "N:1 collapse refused on a same-valued terminology (apply + dry-run)")

    dry = _restore(client, archive_ab, url_ns=tgt, mode="fresh", dry_run=True,
                   namespace_map={ns_a: tgt, ns_b: tgt},
                   expect_writes=[tgt])
    dry_refused = dry.get("status") in ("refused", "failed")
    c.check("PL-JOB", dry_refused,
            f"the DRY RUN refuses the collapse rather than previewing a write "
            f"the apply would reject (status={dry.get('status')})")

    applied = _restore(client, archive_ab, url_ns=tgt, mode="fresh",
                       namespace_map={ns_a: tgt, ns_b: tgt},
                       expect_writes=[tgt])
    c.check("PL-JOB", applied.get("status") in ("refused", "failed"),
            f"the apply refuses the collapse (status={applied.get('status')})")

    # A refusal that already wrote half the data is not a refusal.
    for template in ("MATRIX_SPECIMEN", "MATRIX_SAMPLE"):
        landed = _list_docs(client, tgt, template)
        c.check("PL-DATA", not landed,
                f"nothing partial landed in {tgt} for {template} ({len(landed)})")
    return c


def cell_r04_cli_export_engine_restore(
    client: WipClient, builder: Any, target: Any, ns: str,
) -> Cell:
    """R-04: the CLI-export → engine-restore seam, on the live stack.

    The C layer guards this seam with a golden fixture
    (WIP-Toolkit test_round_trip::test_golden_round_trip — CLI archive read
    by the engine, resolution fidelity without the original session's
    caches). This cell runs the REAL wip-toolkit binary over the network
    against the live install, then feeds its archive to the live engine's
    id-preserving restore door. What only L can see: the CLI's HTTP
    collection against real services (pagination, auth, blob download from
    MinIO) and the engine reading an archive a different writer process
    produced.

    Export shape: --include-inactive (the CASE-666 shape — the fixture's
    deactivated MATRIX_SPECIMEN v3 and the docs pinned to it must make the
    trip), --include-files (the blob half the C round-trip cannot cover —
    its harness has no MinIO), --skip-closure (the archive stays
    namespace-self-contained; cross-namespace refs resolve against the
    intact live NS-B and wip, exactly as they would in a same-instance DR).
    """
    c = Cell("R-04", "CLI export fed to the engine's id-preserving restore")

    cli = Path(sys.executable).parent / "wip-toolkit"
    if not c.check("PL-JOB", cli.is_file(),
                   f"the wip-toolkit CLI is installed in this venv ({cli})"):
        return c

    # Pre-state: everything fidelity is asserted against after the trip.
    before_ids = sorted(d["document_id"] for d in _all_docs_serialized(client, ns))
    before = builder._count_namespace(ns)
    tpl_versions_before = {
        v["version"]: v["status"]
        for v in client.get(
            "/api/template-store/templates/by-value/MATRIX_SPECIMEN/versions",
            params={"namespace": ns},
        ).get("items", [])
    }
    c.check("PL-DATA", "inactive" in tpl_versions_before.values(),
            f"precondition: a deactivated template version exists to carry "
            f"(versions={tpl_versions_before})")
    src_files = client.get(
        "/api/document-store/files",
        params={"namespace": ns, "page_size": 100},
    ).get("items", [])
    if not c.check("PL-FILE", bool(src_files),
                   f"precondition: the namespace still carries its blob(s) "
                   f"({len(src_files)})"):
        return c
    blobs_before = {
        f["file_id"]: client.get_bytes(
            f"/api/document-store/files/{f['file_id']}/content")
        for f in src_files
    }

    # The real CLI, over the network. --proxy + the install's public host.
    parsed_url = urlparse(target.base_url)
    with tempfile.TemporaryDirectory(prefix="r04-cli-export-") as tmp:
        zip_path = Path(tmp) / f"{ns}.zip"
        cmd = [
            str(cli),
            "--host", parsed_url.hostname,
            "--proxy", "--port", str(parsed_url.port or 443),
            "--api-key", target.api_key,
            "--no-verify-ssl",
            "export", ns, str(zip_path),
            "--include-inactive", "--include-files", "--skip-closure",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if not c.check("PL-JOB", proc.returncode == 0 and zip_path.is_file(),
                       f"CLI export exits 0 and writes the archive "
                       f"(rc={proc.returncode}, tail={proc.stderr[-300:] if proc.returncode else 'ok'})"):
            return c
        archive = zip_path.read_bytes()
    parsed = _parse_archive(archive)
    c.check("PL-DATA", parsed.blob_count == len(src_files),
            f"the CLI archive carries a blob per file "
            f"({parsed.blob_count}/{len(src_files)})")

    # Catastrophic loss, then the engine reads what the CLI wrote.
    _drop_ns(client, ns)
    job = _restore(client, archive, url_ns=ns, mode="restore")
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"engine id-preserving restore of the CLI archive completed "
                   f"(status={job.get('status')}, err={job.get('error')})"):
        return c

    after_ids = sorted(d["document_id"] for d in _all_docs_serialized(client, ns))
    c.check("PL-DATA", after_ids == before_ids,
            f"every document id preserved verbatim through the cross-writer "
            f"trip ({len(before_ids)} docs)")
    after = builder._count_namespace(ns)
    diffs = {k: (before.get(k), after.get(k)) for k in CONSERVED_KEYS
             if before.get(k) != after.get(k)}
    c.check("PL-DATA", not diffs,
            f"all conserved counts match the pre-drop namespace (diffs={diffs})")
    tpl_versions_after = {
        v["version"]: v["status"]
        for v in client.get(
            "/api/template-store/templates/by-value/MATRIX_SPECIMEN/versions",
            params={"namespace": ns},
        ).get("items", [])
    }
    c.check("PL-DATA", tpl_versions_after == tpl_versions_before,
            f"template version statuses preserved — the deactivated version "
            f"stays inactive (before={tpl_versions_before}, "
            f"after={tpl_versions_after})")
    c.check("PL-REG",
            _value_form_resolves(client, ns, "terminologies", "terminology",
                                 "MATRIX_COLOR"),
            "restored terminology resolves by value-form — the CLI-exported "
            "registry entries were re-claimed, not re-minted")
    blob_match = all(
        client.get_bytes(f"/api/document-store/files/{fid}/content") == blob
        for fid, blob in blobs_before.items()
    )
    c.check("PL-FILE", blob_match,
            f"every blob byte-identical after the CLI-export half of the trip "
            f"({len(blobs_before)} blob(s))")
    return c


def cell_r03_cross_instance_dr(
    client: WipClient, dr_client: WipClient, builder: Any, dr_counter: Any,
    archive_ab: bytes, ns_a: str, ns_b: str,
) -> Cell:
    """R-03: cross-instance disaster recovery — the archive is the only thing
    that crosses.

    Every other restore cell round-trips within the instance that took the
    backup, where the original registry entries, synonyms, caches and blobs
    still exist even after a namespace drop. Only a SECOND install proves the
    archive is self-sufficient: the id-preserving restore must rebuild
    identity (entries + synonyms re-inserted verbatim), data, refs and blobs
    on an instance that has never seen any of it.

    Boundary stated, not hidden: the fixture's E8 term ref into the shared
    `wip` namespace names a term that exists on the SOURCE install only —
    shared-vocabulary provisioning on a DR target is the operator's runbook,
    not the archive's job (the archive carries the namespaces it was told
    to). The cell asserts the stored value survives verbatim; resolving it
    on the target has no contract to assert.
    """
    c = Cell("R-03", "cross-instance DR — id-preserving restore onto a second install")

    listing = dr_client.get("/api/registry/namespaces", params={"page_size": 500})
    dr_namespaces = {
        n.get("prefix")
        for n in (listing if isinstance(listing, list) else listing.get("items", []))
    }
    for ns in (ns_a, ns_b):
        if not c.check("PL-JOB", ns not in dr_namespaces,
                       f"DR target does not already hold {ns} (fresh instance "
                       "precondition — an id-preserving restore needs empty targets)"):
            return c

    src_ids = {
        ns: sorted(d["document_id"] for d in _all_docs_serialized(client, ns))
        for ns in (ns_a, ns_b)
    }

    job = _restore(dr_client, archive_ab, url_ns=ns_a, mode="restore",
                   expect_writes=[ns_a, ns_b])
    # The DR job lives on the OTHER instance; X-04 sweeps JOBS with the
    # primary client and would 404 on it, saying nothing about field
    # ownership — retract it (the R-11 precedent for cell-owned side
    # effects on the sweep's input).
    JOBS[:] = [j for j in JOBS if j.snapshot.get("job_id") != job.get("job_id")]
    if not c.check("PL-JOB", job.get("status") == "complete",
                   f"id-preserving restore completed on the DR instance "
                   f"(status={job.get('status')}, err={job.get('error')})"):
        return c

    for ns in (ns_a, ns_b):
        dr_ids = sorted(d["document_id"] for d in _all_docs_serialized(dr_client, ns))
        c.check("PL-DATA", dr_ids == src_ids[ns],
                f"every {ns} document id preserved verbatim across instances "
                f"({len(src_ids[ns])} docs)")
        src_counts = builder._count_namespace(ns)
        dr_counts = dr_counter._count_namespace(ns)
        diffs = {k: (src_counts.get(k), dr_counts.get(k)) for k in CONSERVED_KEYS
                 if src_counts.get(k) != dr_counts.get(k)}
        c.check("PL-DATA", not diffs,
                f"all conserved counts match the source instance for {ns} "
                f"(diffs={diffs})")

    # Cross-namespace refs: the copy's stored qualified strings must
    # dereference ON THE DR INSTANCE — GET the exact stored string, the
    # read door the platform documents for the qualified form.
    specs = _list_docs(dr_client, ns_a, "MATRIX_SPECIMEN")
    spec1 = next((d for d in specs
                  if (d.get("data") or {}).get("specimen_code") == "SPEC-1"), None)
    if c.check("PL-DATA", spec1 is not None,
               "SPEC-1 present on the DR instance"):
        ref = (spec1.get("data") or {}).get("primary_sample") or ""
        c.check("PL-DATA", ref.startswith(f"{ns_b}:"),
                f"the cross-namespace ref survives in qualified form ({ref!r})")
        try:
            target_doc = dr_client.get(f"/api/document-store/documents/{ref}")
            resolves = target_doc.get("namespace") == ns_b
        except ApiError:
            resolves = False
        c.check("PL-REG", resolves,
                f"the exact stored ref string dereferences on the DR instance "
                f"({ref!r} -> {ns_b})")
        wip_ref = (spec1.get("data") or {}).get("time_unit")
        c.check("PL-DATA", bool(wip_ref),
                f"the shared-vocabulary term value survives verbatim "
                f"({wip_ref!r}) — resolving it needs the wip vocabulary "
                "provisioned on the DR target (operator runbook, not archive)")

    c.check("PL-REG",
            _value_form_resolves(dr_client, ns_a, "terminologies", "terminology",
                                 "MATRIX_COLOR"),
            "value-form resolution works on the DR instance — the archive's "
            "registry entries AND synonyms were rebuilt there")

    src_files = client.get(
        "/api/document-store/files",
        params={"namespace": ns_a, "page_size": 100},
    ).get("items", [])
    if c.check("PL-FILE", bool(src_files),
               f"the source namespace still carries its blob(s) ({len(src_files)})"):
        match = True
        for f in src_files:
            fid = f["file_id"]
            src_bytes = client.get_bytes(f"/api/document-store/files/{fid}/content")
            try:
                dr_bytes = dr_client.get_bytes(f"/api/document-store/files/{fid}/content")
            except ApiError:
                match = False
                break
            if dr_bytes != src_bytes:
                match = False
                break
        c.check("PL-FILE", match,
                f"every blob is byte-identical on the DR instance, under the "
                f"same file id ({len(src_files)} blob(s))")
    return c


# --------------------------------------------------------------------------- #
# Perturbation cells (F-*) — opt-in via --allow-perturb
#
# These cells change the TARGET INSTALL's runtime state (scaling a service
# to zero, killing a pod), not just the runner's own namespaces. Like B-03
# they are gated: anything else using the install sees the disruption. They
# require a k8s install and a working kubectl context for its cluster.
# --------------------------------------------------------------------------- #


def _kubectl(k8s_ns: str, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "-n", k8s_ns, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _pods_for(k8s_ns: str, svc_label: str) -> list[str]:
    proc = _kubectl(
        k8s_ns, "get", "pods",
        "-l", f"app.kubernetes.io/name={svc_label}",
        "-o", "jsonpath={range .items[*]}{.metadata.name}:{.status.phase} {end}",
    )
    return [p for p in proc.stdout.split() if p]


def _wait_until(what: str, pred, *, timeout_s: float, interval_s: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval_s)
    print(f"    [perturb] timed out waiting for {what} ({timeout_s:.0f}s)")
    return False


def _api_answers(client: WipClient, path: str, params: dict | None = None) -> bool:
    try:
        client.get(path, params=params or {})
        return True
    except (ApiError, httpx.HTTPError):
        return False


def cell_f06_reporting_down(
    client: WipClient, archive: bytes, tgt: str, k8s_ns: str,
) -> Cell:
    """F-06: a restore with reporting-sync STOPPED completes with warnings,
    and the reporting layer backfills after an explicit force.

    The engine's reporting phase is a precondition-checked optional: when
    reporting-sync is unreachable it must disable that phase and say so
    loudly, never fail the restore — the data plane's DR cannot be hostage
    to the analytics plane. But the restore writes Mongo directly, so no
    NATS events exist for reporting-sync to catch up from when it returns:
    the gap is real, visible in parity, and closed only by the explicit
    force batch-sync (drop-and-rebuild, namespace-scoped). The C layer pins
    the warn-and-disable half; only L can run the full arc — real scale-down,
    real restore, real recovery.
    """
    c = Cell("F-06", "restore with reporting-sync stopped; parity backfills after force")

    if not c.check("PL-JOB", _kubectl(k8s_ns, "get", "deployment", "wip-reporting-sync").returncode == 0,
                   f"kubectl reaches deployment wip-reporting-sync in {k8s_ns}"):
        return c

    _kubectl(k8s_ns, "scale", "deployment", "wip-reporting-sync", "--replicas=0")
    try:
        if not c.check("PL-JOB",
                       _wait_until("reporting-sync pods gone",
                                   lambda: not _pods_for(k8s_ns, "reporting-sync"),
                                   timeout_s=60),
                       "reporting-sync scaled to zero (pods gone)"):
            return c

        job = _restore(client, archive, url_ns=tgt, mode="fresh", target_ns=tgt)
        if not c.check("PL-JOB", job.get("status") == "complete",
                       f"restore completes although reporting-sync is down "
                       f"(status={job.get('status')}, err={job.get('error')})"):
            return c
        warnings = [w for w in (job.get("warnings") or []) if "reporting" in str(w).lower()]
        c.check("PL-JOB", bool(warnings),
                f"the job says loudly that reporting was skipped "
                f"({len(job.get('warnings') or [])} warning(s), "
                f"reporting-related: {len(warnings)})")
    finally:
        # The install must get its reporting plane back even if an assert
        # above returned early — a cell must not leave the target degraded.
        _kubectl(k8s_ns, "scale", "deployment", "wip-reporting-sync", "--replicas=1")

    if not c.check("PL-REP",
                   _wait_until("reporting-sync back",
                               lambda: _api_answers(client, "/api/reporting-sync/parity",
                                                    {"namespace": tgt}),
                               timeout_s=180, interval_s=2.0),
                   "reporting-sync answers again after scale-up"):
        return c

    # The first requests after the scale-up land in the window where the
    # ingress endpoints have just repopulated — a keep-alive connection can
    # be reset mid-flight there, which is a transport blip, not a finding.
    # Retry those two calls rather than let one reset fail the cell.
    parity: dict = {}
    for _ in range(5):
        try:
            parity = client.get("/api/reporting-sync/parity",
                                params={"namespace": tgt, "include_counts": "true"})
            break
        except (ApiError, httpx.HTTPError):
            time.sleep(2.0)
    c.check("PL-REP", bool(parity) and not parity.get("ok"),
            f"the gap is VISIBLE before the force — parity not ok for the "
            f"restored namespace (ok={parity.get('ok')}, "
            f"schema_present={parity.get('schema_present')})")

    jobs: list | None = None
    for _ in range(5):
        try:
            jobs = client.post("/api/reporting-sync/sync/batch",
                               params={"namespace": tgt, "force": "true"})
            break
        except (ApiError, httpx.HTTPError):
            time.sleep(2.0)
    c.check("PL-REP", bool(jobs),
            f"force batch-sync accepted for {tgt} ({len(jobs or [])} job(s))")

    final: dict = {}
    def _parity_ok() -> bool:
        nonlocal final
        try:
            final = client.get("/api/reporting-sync/parity",
                               params={"namespace": tgt, "include_counts": "true"})
        except (ApiError, httpx.HTTPError):
            return False
        return bool(final.get("ok"))
    c.check("PL-REP", _wait_until("parity ok after force", _parity_ok, timeout_s=120, interval_s=2.0),
            f"parity converges after the force backfill "
            f"(ok={final.get('ok')}, tables={final.get('table_count')}, "
            f"mismatches={final.get('count_mismatches')})")

    hit_total = 0
    def _fts_hits() -> bool:
        nonlocal hit_total
        try:
            resp = client.post("/api/reporting-sync/search",
                               json_body={"query": "richly indexed",
                                          "types": ["document"],
                                          "namespace": tgt,
                                          "template": "MATRIX_SPECIMEN"})
        except (ApiError, httpx.HTTPError):
            return False
        hit_total = ((resp.get("results") or {}).get("document") or {}).get("total") or 0
        return hit_total > 0
    c.check("PL-REP", _wait_until("FTS hit after force", _fts_hits, timeout_s=60, interval_s=2.0),
            f"full-text search finds the restored document after the "
            f"backfill ({hit_total} hit(s))")
    return c


def cell_f05_crash_mid_restore(
    client: WipClient, builder: Any, archive: bytes, src_a: str, src_b: str,
    tgt_a: str, tgt_b: str, k8s_ns: str,
) -> Cell:
    """F-05: kill the engine mid-fresh-restore; reserved ids stay invisible;
    the documented recovery converges.

    The C layer asserts the END state of a completed run ('activated at the
    end of the run: reserved entries do not resolve, so a namespace left
    reserved would be invisible' — test_remap_integration). The crash half
    of that promise had never been exercised by an actually-interrupted
    run: this cell deletes the document-store pod while the restore is
    mid-flight, then checks the wreckage tells no lies — the job never
    claims success, whatever landed does NOT resolve through the Registry
    (reserved, not activated), a blind re-run refuses the dirty target, and
    the documented recovery (drop the target, re-run) converges to the full
    corpus.
    """
    c = Cell("F-05", "engine killed mid-fresh-restore; reserved invisible; re-run converges")

    if not c.check("PL-JOB", _kubectl(k8s_ns, "get", "deployment", "wip-document-store").returncode == 0,
                   f"kubectl reaches deployment wip-document-store in {k8s_ns}"):
        return c

    # Interrupting a live run is a race against physics: this fixture's
    # fresh restore completes in ~2-4s, and a kubectl round-trip to the
    # cluster costs ~0.5-1.5s on its own — three earlier shapes of this cell
    # ("wait for mid-flight, then kill", graceful delete, bigger archive)
    # all lost that race and "interrupted" runs that were already done. So:
    # SIGKILL (--grace-period=0 --force; a graceful drain lets the run
    # finish inside the grace window), fired the moment the POST is
    # accepted, from a non-blocking Popen against a pre-resolved pod name —
    # and the whole sequence RETRIES when the run still outran the kill,
    # failing honestly only when three attempts in a row could not land an
    # interrupt.
    ns_map = {src_a: tgt_a, src_b: tgt_b}
    job_id, after, attempts = "", {}, 0
    while attempts < 3:
        attempts += 1
        for tgt in (tgt_a, tgt_b):
            with contextlib.suppress(ApiError):
                _drop_ns(client, tgt)
        old_pods = {p.split(":", 1)[0] for p in _pods_for(k8s_ns, "document-store")}
        if not old_pods:
            break
        pod_name = next(iter(old_pods))
        start = _start_restore(client, archive, url_ns=tgt_a, mode="fresh",
                               namespace_map=ns_map)
        if "job_id" not in start:
            break
        job_id = start["job_id"]
        killer = subprocess.Popen(
            ["kubectl", "-n", k8s_ns, "delete", "pod", pod_name,
             "--grace-period=0", "--force", "--wait=false"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        killer.wait(timeout=30)

        # Wait for a REPLACEMENT pod, not merely an answering API: a
        # draining pod keeps serving, so an is-it-back probe can pass
        # against the very pod that is about to die and strand every later
        # call on a 503 (this cell's first run stranded X-04 exactly so).
        def _replacement_running(old_pods: set[str] = old_pods) -> bool:
            pods = _pods_for(k8s_ns, "document-store")
            return any(
                p.split(":", 1)[0] not in old_pods and p.endswith(":Running")
                for p in pods
            )
        if not _wait_until("replacement pod Running", _replacement_running,
                           timeout_s=180, interval_s=2.0):
            break
        if not _wait_until(
                "document-store API back",
                lambda job_id=job_id: _api_answers(
                    client, f"/api/document-store/backup/jobs/{job_id}"),
                timeout_s=120, interval_s=2.0):
            break
        after = client.get(f"/api/document-store/backup/jobs/{job_id}")
        if after.get("status") != "complete":
            break
        # The run outran the kill — scrub this attempt's completed job and
        # try again. (Not registered in JOBS, so nothing to retract.)
        with contextlib.suppress(ApiError):
            client.delete(f"/api/document-store/backup/jobs/{job_id}")
        job_id = ""

    if not c.check("PL-JOB", bool(job_id) and bool(after),
                   f"an interruptible run was achieved (attempt {attempts})"):
        return c
    c.check("PL-JOB", after.get("status") != "complete",
            f"the interrupted job never claims success "
            f"(status={after.get('status')}, phase={after.get('phase')}@"
            f"{after.get('percent')}%, attempt {attempts})")

    landed = (_all_docs_serialized(client, tgt_a)
              + _all_docs_serialized(client, tgt_b))
    if landed:
        lookups = client.post(
            "/api/registry/entries/lookup/by-id",
            json_body=[{"entry_id": d["document_id"]} for d in landed[:5]],
        )
        results = lookups.get("results", [])
        resolved = sum(1 for r in results if r.get("status") == "found")
        # The visibility contract is phase-dependent, per the engine's own
        # ordering ("entries are provisioned as *reserved* ... a single
        # activation at the end makes the whole set visible at once"): a
        # kill BEFORE phase_activate must leave every landed id invisible;
        # a kill AFTER it (synonyms/provenance still pending) must leave
        # the whole set visible; a kill INSIDE the per-target flip is
        # legitimately mixed and only reported.
        killed_phase = str(after.get("phase") or "")
        post_activate = ("phase_activate", "phase_synonyms", "phase_namespace")
        if killed_phase == "phase_activate":
            c.check("PL-REG", True,
                    f"killed inside the activation flip — mixed visibility "
                    f"is legitimate ({resolved}/{len(results)} resolved)")
        elif killed_phase in post_activate:
            c.check("PL-REG", resolved == len(results),
                    f"killed after activation ({killed_phase}) — the set is "
                    f"visible AS A WHOLE ({resolved}/{len(results)} resolved)")
        else:
            c.check("PL-REG", resolved == 0,
                    f"killed before activation ({killed_phase}) — reserved "
                    f"ids stay invisible ({len(results) - resolved}/"
                    f"{len(results)} not_found)")
        blind = _restore(client, archive, url_ns=tgt_a, mode="fresh",
                         namespace_map=ns_map)
        c.check("PL-JOB", blind.get("status") in ("refused", "failed"),
                f"a blind re-run refuses the half-written targets "
                f"(status={blind.get('status')})")
    else:
        c.check("PL-REG", True,
                f"no documents landed before the kill "
                f"({after.get('phase')}@{after.get('percent')}%) — "
                "reserved-visibility and dirty-target checks vacuous this run")

    # The documented recovery: drop the targets, run the same restore again.
    _drop_ns(client, tgt_a)
    _drop_ns(client, tgt_b)
    rerun = _restore(client, archive, url_ns=tgt_a, mode="fresh",
                     namespace_map=ns_map, expect_writes=[tgt_a, tgt_b])
    if not c.check("PL-JOB", rerun.get("status") == "complete",
                   f"the re-run after dropping the targets converges "
                   f"(status={rerun.get('status')}, err={rerun.get('error')})"):
        return c
    for src_ns, tgt in ((src_a, tgt_a), (src_b, tgt_b)):
        src = builder._count_namespace(src_ns)
        got = builder._count_namespace(tgt)
        diffs = {k: (src.get(k), got.get(k)) for k in CONSERVED_KEYS
                 if src.get(k) != got.get(k)}
        c.check("PL-DATA", not diffs,
                f"the converged copy of {src_ns} carries the full corpus "
                f"(diffs={diffs})")
    docs_now = _all_docs_serialized(client, tgt_a)
    resolved = 0
    if docs_now:
        lookups = client.post(
            "/api/registry/entries/lookup/by-id",
            json_body=[{"entry_id": d["document_id"]} for d in docs_now[:5]],
        )
        resolved = sum(1 for r in lookups.get("results", [])
                       if r.get("status") == "found")
        c.check("PL-REG", resolved == len(lookups.get("results", [])),
                f"the re-run's ids are ACTIVE — all {resolved} sampled ids "
                "resolve through the Registry")

    # The crashed job is permanent wreckage by design; the sweep asserts the
    # bookkeeping of jobs that WORKED, so this cell owns removing it from
    # both the install and the sweep (the R-11 precedent).
    with contextlib.suppress(ApiError):
        client.delete(f"/api/document-store/backup/jobs/{job_id}")
    JOBS[:] = [j for j in JOBS if j.snapshot.get("job_id") != job_id]
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


def _k8s_namespace_for_install(install: str | None) -> str | None:
    """The k8s namespace a wip-deploy install renders into, or None.

    Read from the persisted deployer-state (``spec.platform.k8s.namespace``)
    — the same file resolve_target reads the hostname from. None when the
    runner was pointed via --base-url, the state file is unreadable, or the
    install is not a k8s target; the perturbation cells SKIP in every one of
    those cases rather than guessing at a cluster.
    """
    if not install:
        return None
    state_path = Path.home() / ".wip-deploy" / install / "deployment.deployer-state"
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        return None
    k8s = (((state.get("deployment") or {}).get("spec") or {})
           .get("platform") or {}).get("k8s") or {}
    return k8s.get("namespace") or None


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
        "--dr-install",
        help=(
            "wip-deploy install name of a SECOND instance for the R-03 "
            "cross-instance DR cell. The cell id-preserving-restores the "
            "run's multi-namespace archive onto it and asserts identity, "
            "refs and blobs rebuilt there; its namespaces are torn down "
            "with the run's own (honors --keep). Without this flag R-03 "
            "is reported as SKIP."
        ),
    )
    p.add_argument(
        "--allow-perturb", action="store_true",
        help=(
            "run the F-05/F-06 perturbation cells, which change the TARGET "
            "INSTALL's runtime state (scale reporting-sync to zero, delete "
            "the document-store pod). Requires --install pointing at a k8s "
            "install, a kubectl context for its cluster, and an install "
            "nobody else is using — anything else running against it sees "
            "the disruption. Without this flag the cells are reported as "
            "SKIP."
        ),
    )
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

    dr_client: WipClient | None = None
    if args.dr_install:
        try:
            dr_target = resolve_target(
                install=args.dr_install, base_url=None, key_file=None,
                verify_tls=not args.no_verify_tls,
            )
        except TargetError as exc:
            print(f"--dr-install target error: {exc}", file=sys.stderr)
            return 2
        print(f"DR target: --dr-install {args.dr_install} ({dr_target.base_url})")
        dr_client = WipClient(dr_target, timeout=120.0)

    tag = time.strftime("%H%M%S")
    ns_a, ns_b, tgt = f"{tag}-00a", f"{tag}-00b", f"{tag}-00c"
    ns_e = f"{tag}-00e"  # dry-run-parity target (X-01)
    ns_f = f"{tag}-00f"  # second-hop target (X-06)
    ns_j = f"{tag}-00j"  # multi-source fresh target A (R-06)
    ns_k = f"{tag}-00k"  # multi-source fresh target B (R-06)
    ns_l = f"{tag}-00l"  # N:1 collapse target (R-07)
    ns_g = f"{tag}-00g"  # blob-restore target (R-14)
    ns_h = f"{tag}-00h"  # skip_files target (R-14)
    ns_m = f"{tag}-00m"  # reporting-down restore target (F-06)
    ns_n = f"{tag}-00n"  # crash-mid-restore target A (F-05)
    ns_o = f"{tag}-00o"  # crash-mid-restore target B (F-05)
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
            # Bytes kept: the F-05/F-06 perturbation cells restore this
            # pristine single-namespace archive into fresh targets late in
            # the run, after NS-A itself has been dropped and re-restored.
            arch_a_bytes = _backup(client, ns_a)
            arch_a = _parse_archive(arch_a_bytes)
            cells.append(_wrap("B-01", cell_backup_counts, "B-01",
                               "single-namespace real-archive counts (P-SRV1)", arch_a, expected))

            print("[B-02] backing up NS-A + NS-B (multi namespace) ...")
            # Bytes kept as well as the parse: R-02 restores from this very
            # archive at the end of the run, after both namespaces are dropped.
            arch_ab_bytes = _backup(client, ns_a, also=[ns_b])
            arch_ab = _parse_archive(arch_ab_bytes)
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

                # Slice 4 — the two entity classes the matrix carried through
                # backup counts but never through a restore, plus the
                # retained-job door. R-14 and R-11 use NS-A (which holds the
                # E9 file); R-16 checks the reporting layer of the target the
                # fresh-restore spine already populated.
                print("[slice4] R-14 blobs through restore + skip_files ...")
                cells.append(_wrap("R-14", cell_r14_blobs, client,
                                   src_ns=ns_a, tgt_ns=ns_g, skip_tgt_ns=ns_h))
                print("[slice4] R-16 reporting parity + FTS after restore ...")
                cells.append(_wrap("R-16", cell_r16_reporting_parity, client, tgt,
                                   # NS-A holds the fixture's full_text_indexed
                                   # field (MATRIX_SPECIMEN.description), so the
                                   # FTS half searches ITS restored copy (ns_g,
                                   # written by R-14 just above).
                                   fts_ns=ns_g, fts_query="richly indexed",
                                   fts_template="MATRIX_SPECIMEN"))
                print("[slice4] R-11 restore from a retained job ...")
                cells.append(_wrap("R-11", cell_r11_restore_from_retained_job,
                                   client, src_ns=ns_a))

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

                # R-06/R-07 read both sources and write elsewhere, so they run
                # before R-02 — which destroys the sources they need.
                print("[slice5] R-06 multi-source fresh restore, cross-source refs ...")
                cells.append(_wrap("R-06", cell_r06_cross_source_refs, client,
                                   arch_ab_bytes, ns_a, ns_b, ns_j, ns_k))
                print("[slice5] R-07 N:1 collapse refusal ...")
                cells.append(_wrap("R-07", cell_r07_collapse_refused, client,
                                   arch_ab_bytes, ns_a, ns_b, ns_l))

                # R-02 runs LAST of the data cells because it drops BOTH source
                # namespaces and restores them from the multi-namespace archive
                # B-02 took while they were still pristine. Anything depending
                # on NS-A or NS-B has to have run by now.
                print("[slice5] R-02 multi-namespace restore, cross-ns refs ...")
                cells.append(_wrap("R-02", cell_r02_multi_ns_cross_refs,
                                   client, arch_ab_bytes, ns_a, ns_b))

                # R-04 runs after R-02: it needs NS-A populated (R-02 just
                # restored it id-preserved), and it drops + restores NS-A
                # itself, so nothing reading the original sources may follow.
                print("[slice6] R-04 CLI export -> engine restore ...")
                cells.append(_wrap("R-04", cell_r04_cli_export_engine_restore,
                                   client, builder, target, ns_a))

                # R-03 needs a second instance — the whole point of the cell
                # is that the archive, not the source instance, carries the
                # recovery. Opt-in via --dr-install.
                if dr_client is not None:
                    print("[slice7] R-03 cross-instance DR ...")
                    dr_counter = FixtureBuilder(
                        dr_client, ns_a=ns_a, ns_b=ns_b,
                        ontology_file=args.ontology,
                    )
                    cells.append(_wrap("R-03", cell_r03_cross_instance_dr,
                                       client, dr_client, builder, dr_counter,
                                       arch_ab_bytes, ns_a, ns_b))
                else:
                    cells.append(Cell("R-03", "cross-instance DR — id-preserving "
                                      "restore onto a second install")
                                 .skip("needs --dr-install <name>: the cell "
                                       "restores onto a second instance"))

                # F-06/F-05 perturb the install itself, so they are opt-in
                # (like B-03) and run LAST of the data cells: F-06 takes the
                # reporting plane down and F-05 kills the API pod — nothing
                # that expects a healthy install may follow. F-05 after
                # F-06: most disruptive last.
                perturb_ns = _k8s_namespace_for_install(args.install)
                if not args.allow_perturb:
                    perturb_skip = (
                        "needs --allow-perturb: the cell changes the target "
                        "install's runtime state (scale/kill), which anything "
                        "else using the install sees"
                    )
                elif perturb_ns is None:
                    perturb_skip = (
                        "needs a k8s --install (kubectl-reachable) — the "
                        "deployer-state names no k8s namespace for this target"
                    )
                else:
                    perturb_skip = None
                if perturb_skip:
                    cells.append(Cell("F-06", "restore with reporting-sync stopped; "
                                      "parity backfills after force").skip(perturb_skip))
                    cells.append(Cell("F-05", "engine killed mid-fresh-restore; "
                                      "reserved invisible; re-run converges").skip(perturb_skip))
                else:
                    print("[slice6] F-06 restore with reporting-sync stopped ...")
                    cells.append(_wrap("F-06", cell_f06_reporting_down,
                                       client, arch_a_bytes, ns_m, perturb_ns))
                    print("[slice6] F-05 kill engine mid-restore ...")
                    cells.append(_wrap("F-05", cell_f05_crash_mid_restore,
                                       client, builder, arch_ab_bytes,
                                       ns_a, ns_b, ns_n, ns_o, perturb_ns))

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
                teardown(client, [tgt, ns_e, ns_f, ns_g, ns_h, ns_j, ns_k, ns_l,
                                  ns_m, ns_n, ns_o, ns_a, ns_b])
                if dr_client is not None:
                    print("[teardown] deleting DR-instance namespaces ...")
                    teardown(dr_client, [ns_a, ns_b])
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
