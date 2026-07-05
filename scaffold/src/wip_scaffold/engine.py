"""The surface engine (docs/design/scaffold-revamp.md, migration step 3).

Discipline baseline per `wake_rollover.py` (the sibling multi-surface engine) and the design doc
(docs/design/scaffold-revamp.md §"Engine discipline baseline"):
- every write is ATOMIC (tempfile in the destination dir + os.replace)
- runs are IDEMPOTENT (same inputs → same tree, safe to re-run)
- `--dry-run` is a first-class mode: print what each surface would do,
  touch nothing (tested, not incidental)

A Surface is one generated/copied artifact with an explicit lifecycle
policy — the per-surface policy matrix from the design doc, as code:

- REGENERATE     write every run; never reads what it overwrites
- PRESERVE_OR_SET write only when a value is provided; otherwise keep an
                  existing file (warn when neither exists)
- RENDER_REFRESH  create-only for the real target; when the target exists
                  on a refresh, render to a `.refresh` sidecar instead
                  (unless force) — app CLAUDE.md is generated-then-customised;
                  a silent overwrite would destroy app-authored content

Stdlib only — zero third-party deps, so the engine can never be blocked
by venv drift or a dependency pin.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


class Policy(Enum):
    REGENERATE = "regenerate"
    PRESERVE_OR_SET = "preserve_or_set"
    RENDER_REFRESH = "render_refresh"


@dataclass
class Context:
    """Everything a surface producer may need. Values the entry-point bash
    resolved (key file, app metadata) arrive here — the engine never
    re-derives what the wrapper already knows."""

    wip_root: Path
    target_root: Path
    tier3: bool = False
    refresh: bool = False
    dry_run: bool = False
    force_claude_md: bool = False
    tokens: dict[str, str] = field(default_factory=dict)
    role_prefix: str = ""  # PRESERVE_OR_SET input for .session-role
    notes: list[str] = field(default_factory=list)  # engine → operator output


@dataclass
class Surface:
    """One artifact. `produce(ctx)` returns {relative-dest: content-bytes}
    (a surface may own several files, e.g. a directory of commands).
    `wipe_glob` names dest files to remove first (the commands dir contract:
    renamed/retired gene-pool commands must not linger)."""

    name: str
    policy: Policy
    produce: Callable[[Context], dict[str, bytes]]
    rationale: str = ""
    wipe_glob: str | None = None
    executable: bool = False


def _atomic_write(dest: Path, data: bytes, executable: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if executable:
            os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run_surfaces(surfaces: list[Surface], ctx: Context) -> list[str]:
    """Execute (or dry-run) the surface list in order; returns action log."""
    log: list[str] = []

    for s in surfaces:
        files = s.produce(ctx)

        if s.policy is Policy.PRESERVE_OR_SET:
            # produce() returns {} when there is nothing to set — the
            # existing file (if any) is preserved; produce() appends any
            # operator warning to ctx.notes itself.
            pass

        if s.policy is Policy.RENDER_REFRESH and ctx.refresh and not ctx.force_claude_md:
            redirected = {}
            for rel, data in files.items():
                target = ctx.target_root / rel
                if target.exists():
                    redirected[rel + ".refresh"] = data
                    ctx.notes.append(
                        f"NOTICE: existing {rel} left untouched (app-authored content) — fresh "
                        f"render written to {rel}.refresh; merge then delete it, or "
                        f"re-run with --force-claude-md to overwrite."
                    )
                else:
                    redirected[rel] = data
            files = redirected

        wiped: list[Path] = []
        if s.wipe_glob:
            wiped = sorted((ctx.target_root).glob(s.wipe_glob))

        if ctx.dry_run:
            for w in wiped:
                log.append(f"[dry-run] {s.name}: would remove {w.relative_to(ctx.target_root)}")
            for rel in files:
                log.append(f"[dry-run] {s.name}: would write {rel}")
            continue

        for w in wiped:
            w.unlink()
            log.append(f"{s.name}: removed {w.relative_to(ctx.target_root)}")
        for rel, data in files.items():
            _atomic_write(ctx.target_root / rel, data, s.executable)
            log.append(f"{s.name}: wrote {rel}")

    return log
