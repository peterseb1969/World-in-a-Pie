#!/usr/bin/env python3
"""Mechanical drift detection between tracked docs and their KB records.

Docs live in two places: the repo clone and the central KB store. Nothing
was ever responsible for keeping the second in step with the first, so the
KB copy was pushed once at the store's cutover and then left. Twenty-five
days later the KB was still teaching a design pattern the repo had already
retired — an agent reading doctrine through the store learned the thing the
platform had spent a day proving false. This check makes that state visible
and, with --fix, repairable.

Three findings, in descending severity:

1. DRIFTED — a doc has a KB record whose body differs from disk. The worst
   class: the store looks authoritative and is wrong. Gates.
2. ABSENT — a doc under a mandatory prefix has no KB record at all. Less
   dangerous than drift (a reader gets nothing rather than a fiction) but it
   is how "mirroring is mandatory" quietly becomes optional. Gates.
3. UNMIRRORED — a tracked doc outside the mandatory prefixes with no KB
   record. Reported, never gates: which docs belong in the central store is
   a policy decision, not something this script should invent.

## Identity is the hazard, so this script owns both halves of it

READS resolve the repository by `repo_id` — the root-commit fingerprint,
identical across every checkout (CASE-825; falls back to the repo_origin
label for stores predating the backfill) — and match records to disk by
repo-relative tail path. That makes the REPORT true from any clone.

WRITES still address the store by `(repo_origin, path)` with the path
repo-name-prefixed (`World-in-a-Pie/docs/design/v2-index.md`): that is the
store's live search key until CASE-825 phase 4 switches it to
`(repo_id, path)`. Because a write from a differently-named checkout would
miss every lookup and mint duplicates (which is how the corpus was forked
on 2026-07-26, from this very script), --fix carries two refusal guards:
all-ABSENT-against-a-populated-corpus, and checkout-name vs stored-origin
mismatch. Writes also send `repo_id` so the store can switch keys later
without a flag day. The repo-relative path change on writes lands WITH
phase 4, on APP-KB's explicit signal — never before.

## Why it skips instead of failing when the KB is unreachable

A checker that fails the build because a network hop was down teaches people
to ignore it. Absent client or unreachable store → SKIP, loudly, exit 0. The
check gates on drift it actually measured, never on its own inability to
look.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Doc prefixes whose mirroring is mandatory — absence gates, not just drift.
# Design docs are normative records of decisions, which is the case Peter
# made for the central store; other docs/ files are reported and left alone.
MANDATORY_PREFIXES = ("docs/design/",)

# DOCUMENT records require a `kind`; every doc this repo mirrors today is a
# 'guide' (35 of 36; the lone 'playbook' is not under docs/).
DEFAULT_KIND = "guide"

KB_TIMEOUT_S = 120


def kbc_available() -> bool:
    return shutil.which("kbc") is not None


def repo_name(root: Path) -> str:
    """The repo_origin LABEL on a KB document — no longer its identity.

    Derived from the clone directory name, which is what the existing
    records use ('World-in-a-Pie'). Two checkouts of one repository carry
    different directory names, which is how the corpus was forked on
    2026-07-26 (CASE-825: 18 shadow records minted from this very clone).
    Identity now travels as `repo_id` (see repo_fingerprint); this name
    remains only as the human-readable label and as the read fallback for
    stores whose records predate the repo_id backfill.
    """
    return root.resolve().name


def repo_fingerprint(root: Path) -> str:
    """The repository's own identity: its root-commit SHA(s) (CASE-825 #7/#8).

    A pure function of history — identical across every checkout of one
    repository, so a clone directory name can never fork the corpus again.
    Multiple roots are sorted and comma-joined for determinism; the value is
    stored raw rather than hashed so it stays checkable by hand
    (`git rev-list --max-parents=0 HEAD`). Must stay byte-identical to what
    APP-KB's backfill-repo-id.py stamps, or reads and writes stop matching.

    A repo with no commits has no fingerprint — refuse rather than invent
    one (an invented identity is the failure class this replaces).
    """
    roots = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--max-parents=0", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if not roots:
        raise RuntimeError("repository has no commits — no fingerprint exists")
    return ",".join(sorted(roots))


def tail_path(stored: str, origin: str) -> str:
    """A stored KB path reduced to its repo-relative form.

    Strips the leading segment only when it equals the record's own
    repo_origin label — the same derivation rule the store's path_tail
    design uses (CASE-825 #12), so both sides reduce a path identically by
    construction. Already-relative paths (PAPER-1 predates the prefix
    convention) pass through unchanged, and a repo-relative path whose
    first directory happens to match another repo's name cannot be
    mangled, because only the record's own origin is ever stripped.
    """
    prefix = f"{origin}/"
    return stored[len(prefix):] if origin and stored.startswith(prefix) else stored


def canonical_path(root: Path, rel: str) -> str:
    """`<repo>/<repo-relative path>` — the KB's identity for a doc."""
    return f"{repo_name(root)}/{rel}"


def tracked_docs(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "docs"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(p for p in out if p.endswith(".md"))


def _fetch_filtered(root: Path, filt: str) -> dict[str, dict]:
    """All DOCUMENT records matching one --filter, keyed by repo-relative path."""
    records: dict[str, dict] = {}
    page = 1
    while True:
        res = subprocess.run(
            [
                "kbc", "case-fetch.py", "read", "DOCUMENT",
                "--filter", filt,
                "--format", "json", "--page", str(page),
            ],
            capture_output=True, text=True, timeout=KB_TIMEOUT_S, cwd=str(root),
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or f"kbc exited {res.returncode}")
        payload = json.loads(res.stdout)
        for item in payload.get("items", []):
            data = item.get("data", {})
            if data.get("path"):
                records[tail_path(data["path"], data.get("repo_origin") or "")] = data
        if page >= (payload.get("pages") or 1):
            break
        page += 1
    return records


def fetch_kb_docs(root: Path) -> dict[str, dict]:
    """Every KB DOCUMENT for this REPOSITORY, keyed by repo-relative path.

    Resolved by repo_id (checkout-independent) first; a store whose records
    predate the repo_id backfill answers by the repo_origin label instead.
    Raises on any failure; the caller turns that into a skip.
    """
    records = _fetch_filtered(root, f"repo_id={repo_fingerprint(root)}")
    if not records:
        records = _fetch_filtered(root, f"repo_origin={repo_name(root)}")
    return records


def corpus_total(root: Path) -> int:
    """How many DOCUMENT records the store holds overall (unfiltered)."""
    res = subprocess.run(
        [
            "kbc", "case-fetch.py", "read", "DOCUMENT",
            "--page-size", "1", "--format", "json",
        ],
        capture_output=True, text=True, timeout=KB_TIMEOUT_S, cwd=str(root),
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"kbc exited {res.returncode}")
    return int(json.loads(res.stdout).get("total") or 0)


def push(root: Path, rel: str, kind: str, repo_id: str) -> tuple[bool, str]:
    """Mirror one doc. Idempotent: unchanged content is a no-op, drifted
    content updates in place under the same PAPER number.

    `path` stays repo-name-prefixed DELIBERATELY (CASE-825 #10, recorded
    decision): the store's search key is still (repo_origin, path), and a
    repo-relative path before the store strips prefixes and switches to
    (repo_id, path) would miss every lookup and fork the corpus through the
    path component. The path change lands with phase 4, on APP-KB's
    explicit signal — not before.
    """
    res = subprocess.run(
        [
            "kbc", "kb-write.py", "DOCUMENT", rel,
            "--field", f"path={canonical_path(root, rel)}",
            "--field", f"repo_origin={repo_name(root)}",
            "--field", f"repo_id={repo_id}",
            "--field", f"kind={kind}",
        ],
        capture_output=True, text=True, timeout=KB_TIMEOUT_S, cwd=str(root),
    )
    msg = (res.stdout or res.stderr).strip().splitlines()
    return res.returncode == 0, (msg[-1] if msg else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--output", type=Path, help="write findings as JSON")
    ap.add_argument(
        "--strict", action="store_true",
        help="exit 1 on drifted docs or absent mandatory docs",
    )
    ap.add_argument(
        "--fix", action="store_true",
        help="push drifted and absent docs to the KB (writes to the store)",
    )
    args = ap.parse_args()
    root = args.root.resolve()

    findings: dict = {
        "skipped": None, "drifted": [], "absent": [], "unmirrored": [], "in_sync": 0,
    }

    if not kbc_available():
        findings["skipped"] = "kb client (kbc) not installed on this machine"
        print(f"  SKIP kb doc drift — {findings['skipped']}")
        if args.output:
            args.output.write_text(json.dumps(findings, indent=2) + "\n")
        return 0

    try:
        kb = fetch_kb_docs(root)
    except Exception as exc:
        findings["skipped"] = f"kb unreachable: {type(exc).__name__}: {exc}"
        print(f"  SKIP kb doc drift — {findings['skipped']}")
        if args.output:
            args.output.write_text(json.dumps(findings, indent=2) + "\n")
        return 0

    for rel in tracked_docs(root):
        record = kb.get(rel)
        mandatory = rel.startswith(MANDATORY_PREFIXES)
        if record is None:
            bucket = "absent" if mandatory else "unmirrored"
            findings[bucket].append(rel)
            continue
        disk = (root / rel).read_text()
        if (record.get("body") or "") == disk:
            findings["in_sync"] += 1
        else:
            findings["drifted"].append({
                "path": rel,
                "paper_number": record.get("paper_number"),
                "kb_len": len(record.get("body") or ""),
                "disk_len": len(disk),
            })

    for d in findings["drifted"]:
        print(
            f"  DRIFTED {d['path']} — kb PAPER-{d['paper_number']} has "
            f"{d['kb_len']} chars, disk has {d['disk_len']}"
        )
    for rel in findings["absent"]:
        print(f"  ABSENT {rel} — mandatory prefix, no kb record")
    if findings["unmirrored"]:
        print(
            f"  note: {len(findings['unmirrored'])} tracked docs outside "
            f"{'/'.join(MANDATORY_PREFIXES)} are not mirrored (not gated)"
        )
    if not findings["drifted"] and not findings["absent"]:
        print(f"  kb doc drift: clean ({findings['in_sync']} docs in sync)")

    if args.fix:
        targets = [d["path"] for d in findings["drifted"]] + findings["absent"]

        # Guard 1 — all-ABSENT against a populated corpus. Zero records for
        # a repository that plainly has papers means a misidentified clone
        # far more often than a genuinely unmirrored repo; mirroring from
        # here minted 18 shadow records once (CASE-825). Refuse loudly —
        # this guard survives the NEXT way identity gets mis-derived, which
        # repo_id alone does not.
        if targets and not kb:
            try:
                total = corpus_total(root)
            except Exception:
                total = -1
            if total != 0:
                print(
                    "  --fix REFUSED: zero KB records match this repository "
                    f"(repo_id and repo_origin '{repo_name(root)}') while the "
                    f"corpus holds {total if total >= 0 else 'an unknown number of'} "
                    "documents. An all-ABSENT result on a populated corpus is "
                    "almost certainly a misidentified checkout, and mirroring "
                    "would fork the corpus. Verify the clone and the identity "
                    "fields before writing anything (CASE-825)."
                )
                return 2

        # Guard 2 — this checkout is not the one the records were written
        # from. Until the store's search key moves to (repo_id, path)
        # (CASE-825 phase 4), writes are matched by (repo_origin, path);
        # pushing from a checkout whose directory name differs from the
        # stored label misses every lookup and mints duplicates.
        stored_origins = {r.get("repo_origin") for r in kb.values() if r.get("repo_origin")}
        if targets and kb and repo_name(root) not in stored_origins:
            print(
                f"  --fix REFUSED: this checkout is named '{repo_name(root)}' "
                f"but the repository's records carry repo_origin "
                f"{sorted(stored_origins)}. Until the store keys writes by "
                "repo_id (CASE-825 phase 4), pushing from here would mint "
                "duplicates instead of updating. Run --fix from the checkout "
                "matching the stored origin."
            )
            return 2

        print(f"  --fix: pushing {len(targets)} docs to the kb store...")
        fingerprint = repo_fingerprint(root)
        failures = []
        for rel in targets:
            ok, msg = push(root, rel, DEFAULT_KIND, fingerprint)
            print(f"    {'ok ' if ok else 'FAIL'} {rel} — {msg}")
            if not ok:
                failures.append(rel)
        if failures:
            print(f"  --fix: {len(failures)} push(es) failed: {', '.join(failures)}")
            return 1
        # Re-report against the store rather than assuming the pushes stuck.
        findings["fixed"] = targets
        print("  --fix: done; re-run without --fix to confirm against the store")

    if args.output:
        args.output.write_text(json.dumps(findings, indent=2) + "\n")

    if args.strict and not args.fix and (findings["drifted"] or findings["absent"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
