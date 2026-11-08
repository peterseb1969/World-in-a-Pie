#!/usr/bin/env python3
"""Fail a commit that makes the audited Python more complex.

The quality audit measures cyclomatic complexity (radon) only when a release
is tagged, so nothing on develop noticed functions growing between releases:
a function decomposed from CC 63 in May was back at CC 55 six months later,
and the count of functions ranked C or worse doubled against its baseline.
This check runs on every commit instead, over the files the commit changes,
and compares them before and after on two numbers:

- **C+ blocks**: functions, methods and classes radon ranks C or worse
  (CC above 10), counted exactly as the audit counts them;
- **excess**: the sum of (CC - 10) over the C+ functions and methods, so a
  commit that splits one very complex function into several moderately
  complex ones is credited for the complexity it removed, even when the
  count of C+ blocks rises. Classes are left out: radon rates a class by the
  average of its methods, which the excess already holds.

The commit fails when the count rises without the excess falling (a new
C+ function that splits nothing), or when the excess rises by more than
EXCESS_SLACK. The slack lets a fix add a guard branch or an `and x` check to a
function that is already complex (radon counts each as +1) without having to
refactor it in the same commit; growth past it does not pass. The numbers are summed over all changed files together, not per file,
so moving a function into a new module is not new complexity. A file a
commit deletes or renames counts on the "before" side under its old path.

It never blocks an edit that leaves complexity as it was, and it does not
flag the C+ blocks a touched file already had.

Usage:
    check-complexity-ratchet.py [--base REF] [--head REF]
Defaults: --base HEAD~1, --head HEAD. Exit 0 when the commit passes (or
there is nothing to compare), 1 when it fails.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass

from radon.complexity import cc_rank, cc_visit
from radon.visitors import Class

# How much a commit may raise the excess of the files it changes.
EXCESS_SLACK = 3

# The paths the quality audit's radon step scans (scripts/quality-audit.sh,
# step 5). A test keeps the two lists equal.
AUDITED_PATHS = (
    "components/registry/src/",
    "components/def-store/src/",
    "components/template-store/src/",
    "components/document-store/src/",
    "components/reporting-sync/src/",
    "components/ingest-gateway/src/",
    "components/mcp-server/src/",
    "libs/wip-auth/src/",
)


@dataclass
class Measure:
    """C+ blocks and excess complexity of some source."""

    count: int = 0
    excess: int = 0

    def __add__(self, other: Measure) -> Measure:
        return Measure(self.count + other.count, self.excess + other.excess)


def measure(source: str | None) -> Measure:
    """C+ blocks and excess of one file's source; None (absent) is zero.

    cc_visit returns functions, classes and their methods as separate
    blocks, the same blocks `radon cc --json` lists and the audit counts.
    """
    if source is None:
        return Measure()
    result = Measure()
    for block in cc_visit(source):
        if cc_rank(block.complexity) not in "CDEF":
            continue
        result.count += 1
        if not isinstance(block, Class):
            result.excess += block.complexity - 10
    return result


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def file_at(ref: str, path: str) -> str | None:
    """The file's content at ref, or None when it does not exist there."""
    res = _git("show", f"{ref}:{path}")
    return res.stdout if res.returncode == 0 else None


def changed_paths(base: str, head: str) -> list[str]:
    """Audited .py paths the range touches. With --no-renames a rename is a
    delete of the old path plus an add of the new one, so both sides are
    measured."""
    res = _git("diff", "--name-only", "--no-renames", base, head, "--", "*.py")
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "git diff failed")
    return [p for p in res.stdout.split() if p.startswith(AUDITED_PATHS)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="HEAD~1")
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args(argv)

    if _git("rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}").returncode != 0:
        print(f"complexity ratchet: no base commit {args.base!r}; nothing to compare")
        return 0

    paths = changed_paths(args.base, args.head)
    if not paths:
        print("complexity ratchet: no audited Python file changed")
        return 0

    before, after = Measure(), Measure()
    for path in paths:
        try:
            old = measure(file_at(args.base, path))
            new = measure(file_at(args.head, path))
        except SyntaxError as e:
            # A file that does not parse is ruff's to report; it cannot be
            # measured here.
            print(f"  skipped {path}: does not parse ({e.msg}, line {e.lineno})")
            continue
        before, after = before + old, after + new
        if (old.count, old.excess) != (new.count, new.excess):
            print(
                f"  {path}: C+ {old.count} -> {new.count}, "
                f"excess {old.excess} -> {new.excess}"
            )

    print(
        f"complexity ratchet over {len(paths)} changed file(s): "
        f"C+ {before.count} -> {after.count}, excess {before.excess} -> {after.excess}"
    )
    excess_rose = after.excess > before.excess + EXCESS_SLACK
    count_rose_without_credit = after.count > before.count and after.excess >= before.excess
    if excess_rose or count_rose_without_credit:
        print(
            "FAIL: this commit makes the audited code more complex: "
            + ("it adds a function ranked C or worse without removing complexity"
               if count_rose_without_credit else
               f"the excess rises by more than {EXCESS_SLACK}")
            + ". Split the functions it grew, or move the new branches into "
            "small helpers (`python3 -m radon cc <file> -s --min C` lists the "
            "blocks)."
        )
        return 1
    print(f"ok: no new C+ function, excess within +{EXCESS_SLACK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
