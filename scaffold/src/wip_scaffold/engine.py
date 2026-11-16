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
- CREATE_ONLY     a starting point the app then owns (bootstrap templates,
                  the query preset, .env): on a refresh run (--refresh) only
                  targets that do not exist yet are written, so a refresh
                  never overwrites app code. A run without --refresh treats
                  every target as new: a direct engine call against a
                  populated directory must declare the refresh itself
- MERGE_JSON_BASELINE  a scaffold-owned JSON baseline in an app-extended file:
                  three-way merge (last applied baseline, new baseline, the
                  file) keeps app entries, adds and retires baseline entries,
                  records the applied baseline beside the file, and notes
                  every change; a file it cannot read or parse is left
                  untouched and the baseline goes to a `.refresh` sidecar
                  (json_merge.py)

Stdlib only — zero third-party deps, so the engine can never be blocked
by venv drift or a dependency pin.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Policy(Enum):
    REGENERATE = "regenerate"
    PRESERVE_OR_SET = "preserve_or_set"
    RENDER_REFRESH = "render_refresh"
    CREATE_ONLY = "create_only"
    MERGE_JSON_BASELINE = "merge_json_baseline"


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


def _write_mode(dest: Path, executable: bool) -> int:
    """The mode a plain shell write would leave: a new file gets what the
    umask leaves of 0o666 (0o777 when executable). mkstemp creates owner-only
    (0600) whatever the umask, so without this every generated file would land
    unreadable to group and other, and every executable 0700.

    An existing file keeps its mode: a refresh never widens what group and
    other may do, including the owner-only 0600 (and 0711 for executables) an
    engine before this rule left; such a file is widened by hand, with chmod,
    if anyone else needs to read it. The one bit a refresh sets is the
    owner's execute bit on an executable surface: a hook that lost it would
    otherwise stop running without a word, and the pre-commit gate is one."""
    try:
        mode = dest.stat().st_mode & 0o7777
    except FileNotFoundError:
        pass
    else:
        return mode | stat.S_IXUSR if executable else mode
    umask = os.umask(0)
    os.umask(umask)
    mode = 0o666 & ~umask
    if executable:
        # Whoever may read it may run it, and the owner always may.
        mode |= stat.S_IXUSR | (0o111 & ~umask)
    return mode


def _atomic_write(dest: Path, data: bytes, executable: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    mode = _write_mode(dest, executable)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def written(ctx: Context, done: str, would: str) -> str:
    """The verb an operator note uses for a write. Notes are composed in the
    plan phase, which a dry run runs too, so a note about a file the run
    writes must say "would" when the run writes nothing."""
    return would if ctx.dry_run else done


def run_surfaces(surfaces: list[Surface], ctx: Context) -> list[str]:
    """Execute (or dry-run) the surface list in order; returns action log.

    Two phases. The plan phase produces every surface, applies its policy
    and resolves its wipes, touching nothing; only when every surface
    planned without raising does the apply phase wipe and write. A surface
    that refuses (a version not bumped, a missing build) therefore leaves
    the target exactly as it was, and "fix the cause, re-run" is always
    safe — interleaving the two once left an app with new tarballs and a
    package.json still naming the deleted ones. No producer reads another
    surface's output, which is what makes planning everything first sound.
    """
    plan = [(s, *_plan_surface(s, ctx)) for s in surfaces]

    log: list[str] = []
    for s, files, wiped in plan:
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


def _plan_surface(s: Surface, ctx: Context) -> tuple[dict[str, bytes], list[Path]]:
    """What one surface would write and remove; reads the target, never writes it."""
    files = s.produce(ctx)

    # PRESERVE_OR_SET needs no step here: produce() returns {} when there is
    # nothing to set, so the existing file (if any) is preserved, and it
    # appends any operator warning to ctx.notes itself.

    if s.policy is Policy.RENDER_REFRESH and ctx.refresh and not ctx.force_claude_md:
        redirected = {}
        for rel, data in files.items():
            target = ctx.target_root / rel
            if target.exists():
                redirected[rel + ".refresh"] = data
                ctx.notes.append(
                    f"NOTICE: existing {rel} left untouched (app-authored content) — fresh "
                    f"render {written(ctx, 'written', 'would be written')} to {rel}.refresh; "
                    f"merge then delete it, or "
                    f"re-run with --force-claude-md to overwrite."
                )
            else:
                redirected[rel] = data
        files = redirected

    if s.policy is Policy.CREATE_ONLY and ctx.refresh:
        kept = sorted(rel for rel in files if (ctx.target_root / rel).exists())
        if kept:
            ctx.notes.append(
                f"{s.name}: kept {len(kept)} existing file(s) (create-only, the app owns "
                f"them): {', '.join(kept[:5])}{' …' if len(kept) > 5 else ''}"
            )
        files = {rel: data for rel, data in files.items() if rel not in kept}

    if s.policy is Policy.MERGE_JSON_BASELINE:
        files = _merge_json_baselines(s.name, files, ctx)

    if s.wipe_glob and not files:
        # A wipe makes room for what the surface ships. With nothing to ship
        # (a missing or emptied source) it would only delete: refuse, and
        # leave the installed files for the run that can replace them.
        raise RuntimeError(
            f"{s.name}: nothing to write, so its installed files ({s.wipe_glob}) "
            f"were not removed — the source is missing or empty. Restore it, then re-run."
        )
    wiped: list[Path] = sorted(ctx.target_root.glob(s.wipe_glob)) if s.wipe_glob else []
    return files, wiped


def _merge_json_baselines(name: str, files: dict[str, bytes], ctx: Context) -> dict[str, bytes]:
    """Turn each rendered baseline into the files to write: the merged
    target (only when it changes) and the applied-baseline record."""
    from .json_merge import MISSING, baseline_record_path, three_way

    out: dict[str, bytes] = {}
    for rel, data in files.items():
        target = ctx.target_root / rel
        record_rel = baseline_record_path(rel)
        if not target.exists():
            out[rel] = data
            out[record_rel] = data
            continue
        # A file that cannot be read is kept like one that cannot be parsed:
        # its content is unknown, and a rewrite would succeed anyway (the
        # atomic replace needs the directory's permissions, not the file's).
        try:
            cur = json.loads(target.read_text(encoding="utf-8"))
            problem, fix = None, ""
        except OSError as e:
            problem, fix = f"could not be read ({e.strerror or e})", "Make it readable"
        except (ValueError, UnicodeDecodeError) as e:
            problem, fix = f"is not valid JSON ({e})", "Fix it"
        if problem is not None:
            out[rel + ".refresh"] = data
            ctx.notes.append(
                f"NOTICE: {rel} {problem} — left untouched; the scaffold baseline "
                f"{written(ctx, 'was written', 'would be written')} to {rel}.refresh. "
                f"{fix}, then re-run."
            )
            continue
        old = MISSING
        record = ctx.target_root / record_rel
        if record.exists():
            try:
                old = json.loads(record.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                ctx.notes.append(
                    f"Warning: {record_rel} could not be read as JSON — "
                    f"{written(ctx, 'merged', 'would merge')} {rel} without it "
                    f"(baseline entries added, none retired)."
                )
        merged, changes = three_way(old, json.loads(data), cur)
        if merged != cur:
            out[rel] = (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode()
            ctx.notes.append(
                f"NOTICE: {name}: {written(ctx, 'merged', 'would merge')} the scaffold "
                f"baseline into {rel}; app entries kept. "
                + "; ".join(changes)
            )
        out[record_rel] = data
    return out
