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

## The path convention is the hazard, so this script owns it

KB identity for a document is `(repo_origin, path)`, and the stored path is
**repo-prefixed**: `World-in-a-Pie/docs/design/v2-index.md`, not
`docs/design/v2-index.md`. A mirror that computed a bare repo-relative path
would not match the existing record and would mint a SECOND document — the
duplication hazard arriving through the path rather than through the write
mode. That convention lived in no code and no doc; it lives here now, in
canonical_path(), so a caller cannot get it wrong by hand.

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
    """The repo_origin component of a KB document path.

    Derived from the clone directory name, which is what the existing
    records use ('World-in-a-Pie'). Deliberately not from the git remote:
    two remotes are configured (gitea + origin) and they disagree in case
    and host, while the directory name is what the cutover backfill used.
    """
    return root.resolve().name


def canonical_path(root: Path, rel: str) -> str:
    """`<repo>/<repo-relative path>` — the KB's identity for a doc."""
    return f"{repo_name(root)}/{rel}"


def tracked_docs(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "docs"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(p for p in out if p.endswith(".md"))


def fetch_kb_docs(root: Path) -> dict[str, dict]:
    """Every KB DOCUMENT for this repo, keyed by stored path.

    Raises on any failure; the caller turns that into a skip.
    """
    records: dict[str, dict] = {}
    page = 1
    while True:
        res = subprocess.run(
            [
                "kbc", "case-fetch.py", "read", "DOCUMENT",
                "--filter", f"repo_origin={repo_name(root)}",
                "--format", "json", "--page", str(page),
            ],
            capture_output=True, text=True, timeout=KB_TIMEOUT_S, cwd=str(root),
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or f"kbc exited {res.returncode}")
        payload = json.loads(res.stdout)
        items = payload.get("items", [])
        for item in items:
            data = item.get("data", {})
            if data.get("path"):
                records[data["path"]] = data
        if page >= (payload.get("pages") or 1):
            break
        page += 1
    return records


def push(root: Path, rel: str, kind: str) -> tuple[bool, str]:
    """Mirror one doc. Idempotent: unchanged content is a no-op, drifted
    content updates in place under the same PAPER number."""
    res = subprocess.run(
        [
            "kbc", "kb-write.py", "DOCUMENT", rel,
            "--field", f"path={canonical_path(root, rel)}",
            "--field", f"repo_origin={repo_name(root)}",
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
        record = kb.get(canonical_path(root, rel))
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
        print(f"  --fix: pushing {len(targets)} docs to the kb store...")
        failures = []
        for rel in targets:
            ok, msg = push(root, rel, DEFAULT_KIND)
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
