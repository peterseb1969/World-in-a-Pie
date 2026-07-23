#!/usr/bin/env python3
"""Focused backup -> fresh-restore probe for the two SUSPECTED matrix gaps.

Not the Phase 3 runner — a targeted confirm/deny for R-15 and R-13 before
building the full layer-L suite:

- **R-15** prefixed id_config through a fresh restore: what id_config does the
  fresh-restored target get, what ids do its documents carry, and does
  restoring BESIDE the live original collide (the global entry_id index vs a
  per-namespace prefixed counter — the Phase-1 collision lesson).
- **R-13** edge type through a fresh restore: are the relationship document's
  source_ref / target_ref re-pointed to the restored samples' NEW ids, and does
  the versioned:false overwrite-in-place still work after the restore.

Provisions the Phase-1 fixture into fresh namespaces, backs up NS-B (which
carries E11 prefixed id_config + the E4/E5 edge type + docs), fresh-restores it
into a sibling namespace beside the live original, inspects, and tears
everything down. Time makes the namespace prefixes unique per run.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path

from fixtures import FixtureBuilder
from wip_http import ApiError, TargetError, WipClient, resolve_target

DEFAULT_ONTOLOGY = Path.home() / "Downloads" / "onto-test" / "goslim_generic.json"


def _poll_job(client: WipClient, job_id: str, *, timeout_s: float = 90.0) -> dict:
    """Poll a backup/restore job to a terminal state."""
    deadline = time.monotonic() + timeout_s
    last = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/document-store/backup/jobs/{job_id}")
        status = last.get("status")
        if status in ("complete", "failed"):
            return last
        time.sleep(1.5)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s (last={last})")


def _backup_namespace(client: WipClient, ns: str) -> bytes:
    """Server backup of one namespace; return the archive bytes."""
    snap = client.post(
        f"/api/document-store/backup/namespaces/{ns}/backup",
        json_body={"include_files": True},
    )
    job_id = snap["job_id"]
    done = _poll_job(client, job_id)
    if done.get("status") != "complete":
        raise RuntimeError(f"backup of {ns} failed: {done.get('error')}")
    return client.get_bytes(f"/api/document-store/backup/jobs/{job_id}/download")


def _fresh_restore(
    client: WipClient, archive: bytes, *, source_ns: str, target_ns: str
) -> dict:
    """Fresh-restore a single-namespace archive into target_ns. Returns job."""
    snap = client.post(
        f"/api/document-store/backup/namespaces/{target_ns}/restore",
        files={"archive": (f"{source_ns}.zip", archive, "application/zip")},
        data={"mode": "fresh", "target_namespace": target_ns},
    )
    return _poll_job(client, snap["job_id"])


def _manifest_summary(archive: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in names else {}
    return {"format": manifest.get("format_version"), "members": len(names)}


def _list_docs(client: WipClient, ns: str, template_value: str) -> list[dict]:
    resp = client.get(
        "/api/document-store/documents",
        params={
            "namespace": ns,
            "template_value": template_value,
            "latest_only": True,
            "status": "active",
            "page_size": 100,
        },
    )
    return resp.get("items", []) if isinstance(resp, dict) else []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--install", help="wip-deploy install name")
    p.add_argument("--base-url")
    p.add_argument("--key-file")
    p.add_argument("--no-verify-tls", action="store_true")
    p.add_argument("--ontology", type=Path, default=DEFAULT_ONTOLOGY)
    p.add_argument("--prefix", help="namespace time-prefix (default: derived)")
    p.add_argument("--keep", action="store_true", help="skip teardown")
    p.add_argument(
        "--drop-source",
        action="store_true",
        help="delete the source namespace after backup, before restore — "
        "sidesteps the R-15 prefixed-id collision so R-13 can be reached",
    )
    args = p.parse_args(argv)

    try:
        target = resolve_target(
            install=args.install,
            base_url=args.base_url,
            key_file=args.key_file,
            verify_tls=not args.no_verify_tls,
        )
    except TargetError as exc:
        print(f"target error: {exc}", file=sys.stderr)
        return 2

    # A run-unique prefix (no Date.now in scripts; caller passes one, else a
    # coarse monotonic tail keeps it unique enough for a manual probe).
    tag = args.prefix or f"pb{int(time.monotonic()) % 100000:05d}"
    ns_a, ns_b = f"{tag}-00a", f"{tag}-00b"
    target_ns = f"{tag}-00c"  # fresh-restore target, beside the live NS-B

    print(f"target: {target.source}")
    print(f"namespaces: NS-A={ns_a}  NS-B={ns_b}  restore-target={target_ns}")
    findings: list[str] = []

    with WipClient(target, timeout=90.0) as client:
        builder = FixtureBuilder(client, ns_a=ns_a, ns_b=ns_b, ontology_file=args.ontology)
        try:
            print("\n[1/4] provisioning fixture ...")
            builder.build()

            print("[2/4] backing up NS-B ...")
            archive = _backup_namespace(client, ns_b)
            print(f"      archive: {len(archive)} bytes, {_manifest_summary(archive)}")

            # Baseline: the source NS-B sample ids (prefixed id_config).
            src_samples = _list_docs(client, ns_b, "MATRIX_SAMPLE")
            src_ids = sorted(d["document_id"] for d in src_samples)
            print(f"      source NS-B sample ids: {src_ids}")

            if args.drop_source:
                print(f"      --drop-source: deleting live NS-B {ns_b} before restore ...")
                client.delete(
                    f"/api/registry/namespaces/{ns_b}",
                    params={"force": True, "deleted_by": "backup-matrix-probe"},
                )

            print(f"[3/4] fresh-restoring NS-B -> {target_ns} ...")
            try:
                job = _fresh_restore(client, archive, source_ns=ns_b, target_ns=target_ns)
                status = job.get("status")
                print(f"      restore status: {status}"
                      + (f" — {job.get('error')}" if status == "failed" else ""))
            except ApiError as exc:
                status = "api_error"
                job = {"error": str(exc)}
                print(f"      restore API error: {exc}")

            print("[4/4] inspecting R-15 (prefixed id_config) + R-13 (edge type) ...")
            if status == "failed":
                err = str(job.get("error", ""))
                findings.append(
                    f"R-15: fresh-restore of a prefixed-id_config namespace BESIDE "
                    f"the live original FAILED. error={err!r}. If this is a "
                    f"duplicate-key / entry_id collision, it confirms the global "
                    f"entry_id index vs per-namespace prefixed counter clash: a "
                    f"prefixed-id namespace cannot be fresh-restored beside its "
                    f"origin because the re-minted ids reuse the source's prefix."
                )
            elif status == "complete":
                # R-15: what id_config did the target get, and what ids?
                tgt_ns_cfg = client.get(f"/api/registry/namespaces/{target_ns}")
                doc_cfg = (tgt_ns_cfg.get("id_config") or {}).get("documents", {})
                tgt_samples = _list_docs(client, target_ns, "MATRIX_SAMPLE")
                tgt_ids = sorted(d["document_id"] for d in tgt_samples)
                findings.append(
                    f"R-15: restore SUCCEEDED beside original. target id_config.documents="
                    f"{json.dumps(doc_cfg)}; target sample ids={tgt_ids}; "
                    f"source sample ids={src_ids}. "
                    + ("LEAK: target ids carry the SOURCE namespace prefix."
                       if any(ns_b in i for i in tgt_ids)
                       else "target ids do not carry the source prefix.")
                )
                # R-13: edge re-pointing + overwrite-in-place.
                tgt_edges = _list_docs(client, target_ns, "MATRIX_LINKED_TO")
                if not tgt_edges:
                    findings.append("R-13: no relationship document found in the restored namespace.")
                else:
                    edge = tgt_edges[0]
                    sref = edge["data"].get("source_ref")
                    tref = edge["data"].get("target_ref")
                    repointed = sref in tgt_ids and tref in tgt_ids
                    findings.append(
                        f"R-13: restored edge source_ref={sref} target_ref={tref}; "
                        + ("re-pointed to the restored samples' NEW ids (correct)."
                           if repointed
                           else f"NOT re-pointed to target ids {tgt_ids} — dangling/source-pointing.")
                    )
                    # overwrite-in-place (versioned:false): re-POST same endpoints.
                    try:
                        client.post(
                            "/api/document-store/documents",
                            json_body=[{
                                "template_id": "MATRIX_LINKED_TO",
                                "namespace": target_ns,
                                "data": {"source_ref": sref, "target_ref": tref},
                            }],
                        )
                        after = _list_docs(client, target_ns, "MATRIX_LINKED_TO")
                        edge2 = next((e for e in after if e["document_id"] == edge["document_id"]), None)
                        v = edge2.get("version") if edge2 else "?"
                        findings.append(
                            f"R-13: overwrite-in-place after restore -> edge version={v}, "
                            f"count={len(after)} "
                            + ("(versioned:false overwrite works post-restore)."
                               if len(after) == len(tgt_edges) and v == 1
                               else "(UNEXPECTED: overwrite created a new doc or bumped version).")
                        )
                    except ApiError as exc:
                        findings.append(f"R-13: overwrite-in-place after restore ERRORED: {exc}")

        finally:
            if not args.keep:
                print("\ntearing down ...")
                for ns in (target_ns, ns_a, ns_b):
                    with contextlib.suppress(ApiError):
                        client.delete(
                            f"/api/registry/namespaces/{ns}",
                            params={"force": True, "deleted_by": "backup-matrix-probe"},
                        )

    print("\n=== PROBE FINDINGS ===")
    for f in findings:
        print(f"\n• {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
