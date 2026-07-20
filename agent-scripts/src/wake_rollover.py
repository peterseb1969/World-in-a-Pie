#!/usr/bin/env python3
"""Deterministic session rollover — Step A of /wip-wake and /wip-setup's mint.

CASE-604: session rollover is a mechanical state machine (read a field,
compute a timestamp, write a file) that agents used to hand-walk in ~7-8
tool calls from prose instructions. This script IS that state machine; the
slash-command prose now says "run this, then do context recovery (Step B)
with the IDs it prints". Step B stays prose — it genuinely needs judgment.

Identity is LOCAL-FIRST: `.claude/.session-id` is the single source of
truth; kb is a derived mirror. Every control-flow decision reads local
files only. kb writes are tier-gated (skipped silently when
`.claude/kb.json` is absent — tier-2 solo mode is by design) and
warn-and-continue (the local write is authoritative; a re-run or the next
mirror-emitting action retries).

Modes
-----
(default)   /wip-wake Step A — close the prior session, mint a linked
            successor whose `continues_from` points back at it:
              1. Require a prior: read `.claude/.session-id`. Absent -> exit 2
                 ("run /wip-setup").
              2. Require `reports/<prior>/` to exist. Absent -> exit 3 (never
                 fabricate state; legacy shared-path sessions must be moved or
                 closed under the old body first).
              3. Read `status:` from the prior's session.md frontmatter.
                 Missing/malformed frontmatter -> treat as `active` and
                 regenerate well-formed frontmatter from the ID (the prior's
                 own `continues_from` cannot be recovered this way; that loss
                 is accepted and documented here).
                 - `closed` -> skip the close phase entirely (no rewrite, no
                   second summary, no prior mirror).
                 - otherwise -> ONE atomic rewrite: set `status: closed` +
                   `ended_at`, preserve the body, append
                   `## Session Summary — auto-closed by /wip-wake (<ts>)`;
                   then mirror the closed prior to kb.
              4. Mint `<ROLE>-<YYYYMMDD-HHMMSS>` (ROLE from
                 `.claude/.session-role`; missing -> exit 4 with the
                 re-scaffold remediation). Seconds precision is deliberate —
                 it removes the same-minute collision class.
              5. `mkdir reports/<NEW>` (plain, not -p; collision -> re-mint,
                 up to 3 attempts) + write fresh frontmatter with
                 `continues_from: <prior>`.
              6. Atomically overwrite the sentinel (tempfile + os.replace).
              7. Mirror the new session to kb (gateway derives the
                 CONTINUES_FROM edge from the frontmatter; if the prior isn't
                 mirrored yet the edge lands on a later re-mirror).

--fresh     /wip-setup's mint (run AFTER the environment checks pass — a
            failed precheck must never strand an active session):
              - sentinel absent            -> clean fresh start, no
                                              `continues_from`.
              - sentinel present, prior `status: closed`
                                           -> deliberate discontinuous
                                              restart: overwrite the
                                              sentinel, NO `continues_from`.
              - sentinel present, prior active (or state undeterminable)
                                           -> exit 5: run /wip-wake for a
                                              linked session, or
                                              /wip-report session-end first.

--dry-run   Walk the decisions and print the plan; write nothing, call
            nothing.

Output contract (stdout, last lines, machine-readable):
    PRIOR_ID=<id or ->
    NEW_ID=<id>
    PRIOR_SUMMARY=content|stub|absent
Progress/warnings go to stderr.

PRIOR_SUMMARY describes the prior's `## Session Summary` as it stands once
the rollover is done: `content` = a real summary someone wrote, `stub` = a
heading with nothing under it, `absent` = no heading, no prior, or no
readable session.md. A session that died active reports `stub`, because the
close phase has just written the placeholder — that is the dominant case and
the whole point of the signal. `stub` is the caller's cue to backfill from
the durable artifacts; `content` means never overwrite. The state is
reported rather than left for the caller to re-derive, because a
discretionary check is one an agent reliably skips.

Idempotence / partial-failure recovery: every write is atomic; re-running
after a failure converges (an already-closed prior is skipped, kb mirrors
are upserts by session_id). The kb shim is the served client runner
(`~/.cache/wip-kb-client/kb-client.sh`), the same one every role already
uses.

Test hooks (env, used only by the test suite):
    WAKE_ROLLOVER_NOW  freeze the timestamp (YYYYMMDD-HHMMSS)
    WAKE_ROLLOVER_KBC  override the kb shim command
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

EXIT_NO_PRIOR = 2
EXIT_PRIOR_DIR_MISSING = 3
EXIT_NO_ROLE = 4
EXIT_ACTIVE_PRIOR = 5
EXIT_MKDIR = 6

KB_TIMEOUT_S = 60


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def project_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def now_stamp() -> str:
    frozen = os.environ.get("WAKE_ROLLOVER_NOW")
    if frozen:
        return frozen
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def stamp_to_iso(stamp: str) -> str:
    """'20260704-011530' -> '2026-07-04T01:15:30' (naive, NO tz suffix)."""
    d, t = stamp.split("-")
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}T{t[0:2]}:{t[2:4]}:{t[4:6]}"


def atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    """Return (frontmatter dict or None, body). Body excludes the fm block.

    Malformed or absent frontmatter -> (None, full text).
    """
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    fm: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, text[end + 5 :]


def render_frontmatter(fields: list[tuple[str, str]]) -> str:
    lines = ["---"] + [f"{k}: {v}" for k, v in fields] + ["---", ""]
    return "\n".join(lines)


def kb_mirror(root: Path, session_md: Path, dry_run: bool) -> None:
    """Tier-gated, warn-and-continue mirror of session.md to kb."""
    if not (root / ".claude" / "kb.json").exists():
        log("kb mirror: skipped (tier 2 — no .claude/kb.json)")
        return
    if dry_run:
        log(f"kb mirror (dry-run): would push {session_md}")
        return
    shim = os.environ.get("WAKE_ROLLOVER_KBC")
    if shim:
        cmd = [*shim.split(), "kb-write.py", "SESSION", str(session_md)]
    else:
        cmd = [
            "bash",
            str(Path.home() / ".cache" / "wip-kb-client" / "kb-client.sh"),
            "kb-write.py",
            "SESSION",
            str(session_md),
        ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=KB_TIMEOUT_S, cwd=str(root)
        )
        if res.returncode == 0:
            log(f"kb mirror: {res.stdout.strip() or 'ok'}")
        else:
            log(
                "WARNING: kb mirror failed (local write is authoritative; "
                f"re-run converges): {res.stderr.strip() or res.stdout.strip()}"
            )
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"WARNING: kb mirror unreachable (local write is authoritative): {e}")


def summary_state(root: Path, prior_id: str | None) -> str:
    """Classify the prior's `## Session Summary` as content | stub | absent.

    `stub` means a heading with nothing under it — the placeholder close_prior
    writes when a session dies without /wip-report session-end. It reads
    identically to a real close on a `status: closed` check, which is why the
    state is reported explicitly rather than left to be re-derived: an agent
    that has to remember to look will not look.

    Call this AFTER close_prior, so the answer describes the prior as it now
    stands. The dominant case is a session that died active: close_prior has
    just appended the placeholder, and `stub` — the cue to backfill — is
    exactly right. Reading before the append would report `absent` there and
    silently skip the backfill in the very case it exists for.

    Any summary section carrying content wins, so a real summary is never
    misreported as a stub because an append landed after it. Emptiness is
    judged strictly (nothing but whitespace under the heading): a terse but
    real summary counts as content, and no length threshold needs defending.
    """
    if prior_id is None:
        return "absent"
    session_md = root / "reports" / prior_id / "session.md"
    if not session_md.is_file():
        return "absent"
    _, body = parse_frontmatter(session_md.read_text())

    lines = body.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("## Session Summary")]
    if not starts:
        return "absent"

    for start in starts:
        for line in lines[start + 1 :]:
            if line.startswith("## "):
                break
            if line.strip():
                return "content"
    return "stub"


def close_prior(root: Path, prior_id: str, dry_run: bool) -> bool:
    """Close the prior session if it isn't closed already.

    Returns True if this call flipped it (=> caller mirrors), False if it
    was already closed (skip phase — no rewrite, no mirror).
    """
    session_md = root / "reports" / prior_id / "session.md"
    text = session_md.read_text() if session_md.exists() else ""
    fm, body = parse_frontmatter(text)

    if fm is not None and fm.get("status") == "closed":
        log(f"prior {prior_id}: already closed — skipping close phase")
        return False

    close_ts = stamp_to_iso(now_stamp())
    if fm is None:
        # Regenerate well-formed frontmatter from the ID. continues_from is
        # unrecoverable here — acknowledged loss, matching the prose contract.
        role = prior_id.rsplit("-", 2)[0]
        started = stamp_to_iso("-".join(prior_id.rsplit("-", 2)[1:]))
        log(f"prior {prior_id}: frontmatter missing/malformed — regenerating (continues_from lost)")
        fields = [
            ("session_id", prior_id),
            ("role", role),
            ("started_at", started),
            ("status", "closed"),
            ("ended_at", close_ts),
        ]
        new_text = render_frontmatter(fields) + body
    else:
        fm["status"] = "closed"
        fm["ended_at"] = close_ts
        # Preserve original key order; append any new keys at the end.
        seen = []
        raw = text[4 : text.find("\n---\n", 4)]
        for line in raw.splitlines():
            k = line.partition(":")[0].strip()
            if k and k not in seen:
                seen.append(k)
        for k in fm:
            if k not in seen:
                seen.append(k)
        new_text = render_frontmatter([(k, fm[k]) for k in seen if k in fm]) + body

    summary = f"\n## Session Summary — auto-closed by /wip-wake ({close_ts})\n"
    new_text = new_text.rstrip("\n") + "\n" + summary

    if dry_run:
        log(f"dry-run: would close {prior_id} (status=closed, ended_at={close_ts})")
        return True
    atomic_write(session_md, new_text)
    log(f"prior {prior_id}: closed (ended_at {close_ts})")
    return True


def mint_and_create(
    root: Path, role: str, continues_from: str | None, dry_run: bool
) -> str:
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(3):
        stamp = now_stamp()
        new_id = f"{role}-{stamp}"
        new_dir = reports / new_id
        if dry_run:
            log(f"dry-run: would mkdir {new_dir} + write session.md")
            return new_id
        try:
            new_dir.mkdir()  # plain mkdir — collision must surface
        except FileExistsError as e:
            last_err = e
            log(f"collision on {new_id} (attempt {attempt + 1}/3) — re-minting")
            time.sleep(1)
            continue
        fields = [
            ("session_id", new_id),
            ("role", role),
            ("started_at", stamp_to_iso(stamp)),
            ("status", "active"),
        ]
        if continues_from:
            fields.append(("continues_from", continues_from))
        body = f"\n# Session {new_id}\n"
        if continues_from:
            body += f"\nContinues from {continues_from} (via /wip-wake).\n"
        atomic_write(new_dir / "session.md", render_frontmatter(fields) + body)
        return new_id
    print(
        f"Error: could not create a fresh report dir after 3 attempts: {last_err}",
        file=sys.stderr,
    )
    sys.exit(EXIT_MKDIR)


def swap_sentinel(root: Path, new_id: str, dry_run: bool) -> None:
    sentinel = root / ".claude" / ".session-id"
    if dry_run:
        log(f"dry-run: would write sentinel {sentinel} = {new_id}")
        return
    sentinel.parent.mkdir(exist_ok=True)
    atomic_write(sentinel, new_id + "\n")
    log(f"sentinel: {new_id}")


def read_sentinel(root: Path) -> str | None:
    p = root / ".claude" / ".session-id"
    if not p.exists():
        return None
    v = p.read_text().strip()
    return v or None


def read_role(root: Path) -> str:
    p = root / ".claude" / ".session-role"
    if not p.exists() or not p.read_text().strip():
        print(
            "Error: .claude/.session-role is missing (gitignored — fresh "
            "checkouts never have it). Fix: backend clone -> re-run "
            "scripts/setup-backend-agent.sh; app repo -> re-run "
            "scripts/create-app-project.sh <app-dir> --prefix APP-<X> "
            "(repos whose .app-meta records ROLE_PREFIX self-heal without "
            "--prefix). Then retry.",
            file=sys.stderr,
        )
        sys.exit(EXIT_NO_ROLE)
    return p.read_text().strip()


def prior_status(root: Path, prior_id: str) -> str | None:
    """'closed' | 'active' | None (dir or session.md missing)."""
    session_md = root / "reports" / prior_id / "session.md"
    if not session_md.exists():
        return None
    fm, _ = parse_frontmatter(session_md.read_text())
    if fm is None:
        return "active"  # conservative default, matching the prose
    return "closed" if fm.get("status") == "closed" else "active"


def wake(root: Path, dry_run: bool) -> tuple[str, str, str]:
    prior = read_sentinel(root)
    if prior is None:
        print(
            "Error: no prior session found at .claude/.session-id. "
            "Run /wip-setup for a fresh session with no continuation.",
            file=sys.stderr,
        )
        sys.exit(EXIT_NO_PRIOR)

    prior_dir = root / "reports" / prior
    if not prior_dir.is_dir():
        print(
            f"Error: prior session dir reports/{prior}/ not found — refusing "
            "to fabricate state. If this session was staged before the "
            "project-local reports model, either run /wip-report session-end "
            "under the old body or move the legacy shared-path dir into this "
            "repo's reports/. Otherwise restore the dir, or rm "
            ".claude/.session-id and run /wip-setup for a fresh "
            "discontinuous start.",
            file=sys.stderr,
        )
        sys.exit(EXIT_PRIOR_DIR_MISSING)

    role = read_role(root)
    flipped = close_prior(root, prior, dry_run)
    if flipped:
        kb_mirror(root, prior_dir / "session.md", dry_run)
    # After the close: a session that died active has just been given the
    # placeholder, and `stub` is the state the caller must act on.
    prior_summary = summary_state(root, prior)
    if dry_run and flipped and prior_summary == "absent":
        # The close we deliberately didn't perform would have left a stub;
        # report the plan's outcome, not the untouched file.
        prior_summary = "stub"

    new_id = mint_and_create(root, role, continues_from=prior, dry_run=dry_run)
    swap_sentinel(root, new_id, dry_run)
    kb_mirror(root, root / "reports" / new_id / "session.md", dry_run)
    return prior, new_id, prior_summary


def fresh(root: Path, dry_run: bool) -> tuple[str, str, str]:
    role = read_role(root)
    prior = read_sentinel(root)
    prior_summary = summary_state(root, prior)
    if prior is not None:
        status = prior_status(root, prior)
        if status != "closed":
            print(
                f"Error: active session {prior} found at .claude/.session-id. "
                "Run /wip-wake to start a new linked session, or "
                "/wip-report session-end first, then retry --fresh for a "
                "clean discontinuous restart.",
                file=sys.stderr,
            )
            sys.exit(EXIT_ACTIVE_PRIOR)
        log(f"prior {prior} is closed — discontinuous restart (no continues_from)")

    new_id = mint_and_create(root, role, continues_from=None, dry_run=dry_run)
    swap_sentinel(root, new_id, dry_run)
    kb_mirror(root, root / "reports" / new_id / "session.md", dry_run)
    return prior or "-", new_id, prior_summary


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    mode_fresh = "--fresh" in argv
    unknown = [a for a in argv[1:] if a not in ("--fresh", "--dry-run")]
    if unknown:
        print(f"Error: unknown argument(s): {unknown}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 1

    root = project_root()
    if mode_fresh:
        prior, new_id, prior_summary = fresh(root, dry_run)
    else:
        prior, new_id, prior_summary = wake(root, dry_run)

    print(f"PRIOR_ID={prior}")
    print(f"NEW_ID={new_id}")
    print(f"PRIOR_SUMMARY={prior_summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
