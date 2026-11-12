#!/usr/bin/env python3
"""Release-note fragments: check them, preview the unreleased note, cut a release.

Every change that needs a release note adds ONE new file under
docs/releases/unreleased/ and never edits a shared release file. Parallel
lanes then never touch the same file, so release notes cannot conflict on
rebase. There is no docs/releases/unreleased.md any more; `preview` renders
it on demand.

A fragment is markdown with a small frontmatter block:

    ---
    section: fixes
    cases: [1260]
    packages: [wip-client]
    ---

    ### What changed, as a heading

    The entry, exactly as a release note reads it.

- section   required; one of the keys of SECTIONS below.
- cases     optional; case numbers the entry belongs to.
- packages  optional; the shipped packages the change touches (PACKAGES).

The body is one or more `###` entries. It may not contain `#` or `##`
headings outside code fences: the section heading is the assembler's.
Name the file after the case and a short slug (case-1260-fragments.md);
two entries on one case are two files.

Usage:
    release-notes.py check              # validate fragments; refuse unreleased.md
    release-notes.py preview [--out F]  # render the unreleased note
    release-notes.py cut X.Y.Z          # write docs/releases/vX.Y.Z.md, delete fragments

Stdlib only: CI runs `check` with the runner's bare python3.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RELEASES = Path("docs/releases")
FRAGMENTS = RELEASES / "unreleased"
LEGACY = RELEASES / "unreleased.md"

# Release-note sections, in the order a release note prints them.
SECTIONS = {
    "action-required": "Action required / behavior changes (read before upgrading)",
    "new": "New capabilities",
    "fixes": "Fixes",
    "first-start": "What happens on first start after the upgrade",
    "deployment": "Deployment changes",
    "operational": "Operational notes",
}

# Packages shipped to consumers under their own version.
PACKAGES = ("wip-client", "wip-react", "wip-proxy", "wip-archive", "wip-toolkit")

KEYS = ("section", "cases", "packages")
SKIP = {"README.md"}


@dataclass
class Fragment:
    path: Path
    section: str = ""
    cases: list[int] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    body: str = ""


def _parse_list(raw: str) -> list[str]:
    raw = raw.strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        raise ValueError(f"expected a [list], got {raw!r}")
    return [item.strip() for item in raw[1:-1].split(",") if item.strip()]


def _frontmatter_line(frag: Fragment, raw: str) -> str | None:
    """Apply one frontmatter line to frag; return the problem, if any."""
    key, sep, value = raw.partition(":")
    key = key.strip()
    if not sep or key not in KEYS:
        return f"unknown frontmatter line {raw!r} (keys: {', '.join(KEYS)})"
    try:
        if key == "section":
            frag.section = value.strip()
        elif key == "cases":
            frag.cases = [int(c.removeprefix("CASE-")) for c in _parse_list(value)]
        else:
            frag.packages = _parse_list(value)
    except ValueError as exc:
        return f"{key}: {exc}"
    return None


def _value_errors(frag: Fragment) -> list[str]:
    errors = []
    if frag.section not in SECTIONS:
        errors.append(f"section {frag.section!r} is not one of: {', '.join(SECTIONS)}")
    errors.extend(
        f"package {pkg!r} is not one of: {', '.join(PACKAGES)}" for pkg in frag.packages if pkg not in PACKAGES
    )
    return errors


def _body_errors(lines: list[str], first: int) -> list[str]:
    """Headings the assembler owns (# and ##), outside code fences."""
    errors = []
    in_fence = False
    for n, line in enumerate(lines, start=first):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and re.match(r"#{1,2} ", line):
            errors.append(
                f"line {n}: '{line[:40]}' — a fragment may not carry # or ## headings; "
                "use ### for entries (the section heading comes from `section`)"
            )
    return errors


def parse(path: Path) -> tuple[Fragment, list[str]]:
    """Read one fragment; return it with the problems found (empty = valid)."""
    frag = Fragment(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return frag, ["no frontmatter: the file must start with a --- line"]
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return frag, ["frontmatter is not closed with a --- line"]

    errors = [e for raw in lines[1:end] if raw.strip() if (e := _frontmatter_line(frag, raw))]
    errors += _value_errors(frag)
    frag.body = "\n".join(lines[end + 1 :]).strip("\n")
    if not frag.body.strip():
        errors.append("the body is empty")
    errors += _body_errors(lines[end + 1 :], end + 2)
    return frag, errors


def collect(root: Path) -> tuple[list[Fragment], list[str]]:
    """Every fragment under root, sorted by name, plus every problem found."""
    errors: list[str] = []
    if (root / LEGACY).exists():
        errors.append(
            f"{LEGACY} exists. Release notes are fragments now: put the entry in a new "
            f"file under {FRAGMENTS}/ (see {FRAGMENTS}/README.md) and delete {LEGACY}."
        )
    frags: list[Fragment] = []
    directory = root / FRAGMENTS
    if not directory.is_dir():
        return frags, errors
    for path in sorted(directory.iterdir()):
        if path.name in SKIP or path.is_dir():
            continue
        rel = path.relative_to(root)
        if path.suffix != ".md":
            errors.append(f"{rel}: not a .md file")
            continue
        frag, problems = parse(path)
        errors.extend(f"{rel}: {p}" for p in problems)
        frags.append(frag)
    return frags, errors


def assemble(frags: list[Fragment]) -> str:
    """The sections in release order, each fragment's body in file-name order."""
    out: list[str] = []
    for key, heading in SECTIONS.items():
        bodies = [f.body for f in frags if f.section == key]
        if bodies:
            out.append(f"## {heading}\n\n" + "\n\n".join(bodies))
    return "\n\n".join(out) + "\n"


def _fail(errors: list[str]) -> int:
    for err in errors:
        print(f"release-notes: {err}", file=sys.stderr)
    return 1


def cmd_check(root: Path) -> int:
    frags, errors = collect(root)
    if errors:
        return _fail(errors)
    print(f"release-notes: {len(frags)} fragment(s) valid")
    return 0


def cmd_preview(root: Path, out: str | None) -> int:
    frags, errors = collect(root)
    if errors:
        return _fail(errors)
    text = (
        "# WIP — unreleased\n\n"
        "Generated by `scripts/release-notes.py preview` from "
        f"`{FRAGMENTS}/`. Do not edit or commit this rendering.\n\n" + assemble(frags)
    )
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def cmd_cut(root: Path, version: str) -> int:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return _fail([f"version {version!r} is not X.Y.Z"])
    frags, errors = collect(root)
    if errors:
        return _fail(errors)
    if not frags:
        return _fail([f"no fragments under {FRAGMENTS}/ — nothing to release"])
    target = root / RELEASES / f"v{version}.md"
    if target.exists():
        return _fail([f"{target.relative_to(root)} already exists"])
    today = datetime.date.today().isoformat()
    target.write_text(
        f"# WIP v{version}\n\n**Released:** {today}\n\n"
        "<!-- Write the headline here, then curate the sections below. -->\n\n" + assemble(frags),
        encoding="utf-8",
    )
    for frag in frags:
        frag.path.unlink()
    packages = sorted({p for f in frags for p in f.packages})
    print(f"release-notes: wrote {target.relative_to(root)} from {len(frags)} fragment(s)")
    print(f"release-notes: packages changed: {', '.join(packages) or 'none named'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=str(REPO), help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate fragments; refuse docs/releases/unreleased.md")
    p_preview = sub.add_parser("preview", help="render the unreleased note")
    p_preview.add_argument("--out", help="write to this file instead of stdout")
    p_cut = sub.add_parser("cut", help="write docs/releases/vX.Y.Z.md and delete fragments")
    p_cut.add_argument("version", help="X.Y.Z")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.cmd == "check":
        return cmd_check(root)
    if args.cmd == "preview":
        return cmd_preview(root, args.out)
    return cmd_cut(root, args.version)


if __name__ == "__main__":
    sys.exit(main())
