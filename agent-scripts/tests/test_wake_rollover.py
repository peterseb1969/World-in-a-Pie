"""CASE-604 — the deterministic session-rollover state machine.

Every edge case the /wip-wake and /wip-setup prose carried must survive the
extraction into wake_rollover.py; a broken rollover breaks session
continuity for every YAC. Tests run the script as a subprocess against a
tmp project root (CLAUDE_PROJECT_DIR), with the kb shim replaced by a fake
that logs its invocations (WAKE_ROLLOVER_KBC) and the clock frozen where
determinism matters (WAKE_ROLLOVER_NOW).
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "wake_rollover.py"

PRIOR_ID = "BE-YAC-20260701-120000"
FROZEN = "20260704-090000"


def make_project(tmp_path: Path, *, role: str = "BE-YAC", prior_status: str = "active",
                 with_prior: bool = True, with_kb: bool = True,
                 with_frontmatter: bool = True) -> Path:
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / ".session-role").write_text(role + "\n")
    if with_kb:
        (root / ".claude" / "kb.json").write_text("{}")
    if with_prior:
        (root / ".claude" / ".session-id").write_text(PRIOR_ID + "\n")
        d = root / "reports" / PRIOR_ID
        d.mkdir(parents=True)
        if with_frontmatter:
            fm = (
                "---\n"
                f"session_id: {PRIOR_ID}\n"
                "role: BE-YAC\n"
                "started_at: 2026-07-01T12:00:00\n"
                f"status: {prior_status}\n"
                "continues_from: BE-YAC-20260630-080000\n"
                "---\n"
            )
        else:
            fm = ""
        (d / "session.md").write_text(fm + f"\n# Session {PRIOR_ID}\n\nBody text.\n")
    return root


def _write_fake_kbc(fake: Path, log: Path, exit_code: int = 0) -> None:
    fake.write_text(f'#!/bin/bash\necho "$@" >> "{log}"\nexit {exit_code}\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)


def make_fake_kbc(tmp_path: Path, *, exit_code: int = 0) -> tuple[str, Path]:
    log = tmp_path / "kbc-calls.log"
    fake = tmp_path / "fake-kbc.sh"
    _write_fake_kbc(fake, log, exit_code)
    return str(fake), log


def run(root: Path, *args: str, kbc: str | None = None, frozen: str | None = FROZEN):
    """Run the rollover script against a tmp project root, ALWAYS isolated.

    CASE-736 provenance: the kb shim override is unconditional — a caller
    that passes no ``kbc=`` gets a throwaway fake, never the real served
    client. The old shape (unset the override when the argument was absent)
    made forgetting ``kbc=`` a silent production write: the script fell
    through to ``~/.cache/wip-kb-client/kb-client.sh``, which resolved the
    REAL repo config from the pytest CWD, and ~20 suite runs upserted two
    fixture SESSION records onto the live kb. Touching a live instance from
    this suite must be impossible, not merely avoidable.

    ``cwd=root`` is the second layer: anything that resolves config relative
    to the working directory sees the fixture project, not this repo.
    """
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env.pop("WAKE_ROLLOVER_NOW", None)
    if frozen:
        env["WAKE_ROLLOVER_NOW"] = frozen
    if kbc is None:
        fake = root.parent / "default-fake-kbc.sh"
        if not fake.exists():
            _write_fake_kbc(fake, root.parent / "default-kbc-calls.log")
        kbc = str(fake)
    env["WAKE_ROLLOVER_KBC"] = kbc
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, cwd=root,
    )


def out_ids(res) -> dict:
    return dict(
        line.split("=", 1) for line in res.stdout.splitlines() if "=" in line
    )


class TestWakeHappyPath:
    def test_active_prior_closed_and_successor_minted(self, tmp_path):
        root = make_project(tmp_path)
        kbc, log = make_fake_kbc(tmp_path)
        res = run(root, kbc=kbc)
        assert res.returncode == 0, res.stderr

        ids = out_ids(res)
        assert ids["PRIOR_ID"] == PRIOR_ID
        assert ids["NEW_ID"] == f"BE-YAC-{FROZEN}"

        # Prior: closed, ended_at set, other fm keys preserved, one summary.
        prior_text = (root / "reports" / PRIOR_ID / "session.md").read_text()
        assert "status: closed" in prior_text
        assert "ended_at: 2026-07-04T09:00:00" in prior_text
        assert "continues_from: BE-YAC-20260630-080000" in prior_text
        assert "Body text." in prior_text
        assert prior_text.count("## Session Summary — auto-closed") == 1

        # New session: dir, frontmatter, lineage.
        new_text = (root / "reports" / ids["NEW_ID"] / "session.md").read_text()
        assert f"session_id: {ids['NEW_ID']}" in new_text
        assert "status: active" in new_text
        assert f"continues_from: {PRIOR_ID}" in new_text
        assert "started_at: 2026-07-04T09:00:00" in new_text

        # Sentinel swapped atomically.
        assert (root / ".claude" / ".session-id").read_text().strip() == ids["NEW_ID"]

        # kb mirrored: prior (we flipped it) + new = 2 calls.
        calls = log.read_text().splitlines()
        assert len(calls) == 2
        assert PRIOR_ID in calls[0] and ids["NEW_ID"] in calls[1]

    def test_already_closed_prior_skips_close_phase(self, tmp_path):
        root = make_project(tmp_path, prior_status="closed")
        kbc, log = make_fake_kbc(tmp_path)
        before = (root / "reports" / PRIOR_ID / "session.md").read_text()
        res = run(root, kbc=kbc)
        assert res.returncode == 0, res.stderr

        # Prior untouched: no rewrite, no second summary.
        assert (root / "reports" / PRIOR_ID / "session.md").read_text() == before
        # Only the NEW session mirrored (1 call, not 2).
        calls = log.read_text().splitlines()
        assert len(calls) == 1
        assert out_ids(res)["NEW_ID"] in calls[0]

    def test_missing_frontmatter_regenerated(self, tmp_path):
        root = make_project(tmp_path, with_frontmatter=False)
        res = run(root)
        assert res.returncode == 0, res.stderr
        prior_text = (root / "reports" / PRIOR_ID / "session.md").read_text()
        assert prior_text.startswith("---\n")
        assert f"session_id: {PRIOR_ID}" in prior_text
        assert "role: BE-YAC" in prior_text
        assert "started_at: 2026-07-01T12:00:00" in prior_text
        assert "status: closed" in prior_text
        assert "Body text." in prior_text  # body preserved
        # continues_from is unrecoverable in regeneration — documented loss.
        assert "continues_from" not in prior_text
        assert "continues_from lost" in res.stderr


class TestWakeHardStops:
    def test_missing_sentinel(self, tmp_path):
        root = make_project(tmp_path, with_prior=False)
        res = run(root)
        assert res.returncode == 2
        assert "/wip-setup" in res.stderr
        assert not (root / "reports").exists() or not list((root / "reports").iterdir())

    def test_missing_prior_dir(self, tmp_path):
        root = make_project(tmp_path, with_prior=False)
        (root / ".claude" / ".session-id").write_text(PRIOR_ID + "\n")
        res = run(root)
        assert res.returncode == 3
        assert "refusing" in res.stderr.lower() or "not found" in res.stderr
        # Sentinel untouched — never fabricate forward motion.
        assert (root / ".claude" / ".session-id").read_text().strip() == PRIOR_ID

    def test_missing_role(self, tmp_path):
        root = make_project(tmp_path)
        (root / ".claude" / ".session-role").unlink()
        res = run(root)
        assert res.returncode == 4
        # The remediation must name the real re-scaffold commands — the old
        # message pointed at a --refresh flag that no script accepts.
        assert ".session-role is missing" in res.stderr
        assert "--prefix APP-<X>" in res.stderr
        assert "--refresh" not in res.stderr


class TestKbBehaviour:
    def test_bare_run_is_isolated_by_default(self, tmp_path):
        """Tripwire for the isolation default itself: a run() WITHOUT kbc=
        must land its kb mirror on the throwaway fake — never on the real
        served client. If this fails, the suite has regressed to opt-OUT
        isolation, the exact shape that let ~20 runs write fixture SESSION
        records onto the production kb before anyone noticed."""
        root = make_project(tmp_path)
        res = run(root)
        assert res.returncode == 0, res.stderr

        # The default fake logged the mirror calls — the override was set
        # and honored (prior close + new session = 2 calls).
        default_log = root.parent / "default-kbc-calls.log"
        assert default_log.exists(), "default fake shim was never invoked"
        calls = default_log.read_text().splitlines()
        assert len(calls) == 2
        assert PRIOR_ID in calls[0]
        # And the mirror genuinely ran (not skipped by the tier gate).
        assert "tier 2" not in res.stderr

    def test_tier2_no_kb_json_skips_silently(self, tmp_path):
        root = make_project(tmp_path, with_kb=False)
        kbc, log = make_fake_kbc(tmp_path)
        res = run(root, kbc=kbc)
        assert res.returncode == 0, res.stderr
        assert not log.exists()  # shim never invoked
        assert "tier 2" in res.stderr

    def test_kb_failure_warns_and_continues(self, tmp_path):
        root = make_project(tmp_path)
        kbc, _log = make_fake_kbc(tmp_path, exit_code=1)
        res = run(root, kbc=kbc)
        assert res.returncode == 0, res.stderr  # local writes are authoritative
        assert "WARNING" in res.stderr
        ids = out_ids(res)
        assert (root / ".claude" / ".session-id").read_text().strip() == ids["NEW_ID"]


class TestConvergence:
    def test_rerun_after_partial_failure_converges(self, tmp_path):
        """Simulate a crash after the close but before the sentinel swap:
        prior is closed on disk, sentinel still points at it. A re-run must
        skip the close (no double summary) and complete the rollover."""
        root = make_project(tmp_path)
        run(root)  # full run closes prior and swaps sentinel...
        # ...manually rewind the sentinel to simulate the partial state.
        (root / ".claude" / ".session-id").write_text(PRIOR_ID + "\n")
        res = run(root, frozen="20260704-091500")
        assert res.returncode == 0, res.stderr
        prior_text = (root / "reports" / PRIOR_ID / "session.md").read_text()
        assert prior_text.count("## Session Summary — auto-closed") == 1
        assert out_ids(res)["NEW_ID"] == "BE-YAC-20260704-091500"

    def test_mkdir_collision_with_frozen_clock_errors_cleanly(self, tmp_path):
        root = make_project(tmp_path)
        (root / "reports" / f"BE-YAC-{FROZEN}").mkdir(parents=True)
        res = run(root)
        assert res.returncode == 6
        assert "3 attempts" in res.stderr


class TestFreshMode:
    def test_no_sentinel_clean_start(self, tmp_path):
        root = make_project(tmp_path, with_prior=False)
        res = run(root, "--fresh")
        assert res.returncode == 0, res.stderr
        ids = out_ids(res)
        assert ids["PRIOR_ID"] == "-"
        new_text = (root / "reports" / ids["NEW_ID"] / "session.md").read_text()
        assert "continues_from" not in new_text

    def test_closed_prior_discontinuous_restart(self, tmp_path):
        root = make_project(tmp_path, prior_status="closed")
        res = run(root, "--fresh")
        assert res.returncode == 0, res.stderr
        ids = out_ids(res)
        new_text = (root / "reports" / ids["NEW_ID"] / "session.md").read_text()
        assert "continues_from" not in new_text  # discontinuous by design
        assert (root / ".claude" / ".session-id").read_text().strip() == ids["NEW_ID"]

    def test_active_prior_refused(self, tmp_path):
        root = make_project(tmp_path, prior_status="active")
        res = run(root, "--fresh")
        assert res.returncode == 5
        assert "/wip-wake" in res.stderr
        # Nothing written.
        assert (root / ".claude" / ".session-id").read_text().strip() == PRIOR_ID


class TestPriorSummarySignal:
    """CASE-708 — the rollover reports whether the prior's summary is real.

    A bare `## Session Summary — auto-closed by /wip-wake (<ts>)` and a
    written summary are indistinguishable to a `status: closed` check, so the
    empty ones used to stay empty forever: the agent who could have written
    one is gone, and nobody runs session-end against someone else's session.
    Detection is mechanical here so the backfill prose acts on a signal
    instead of remembering to look.
    """

    def _write_body(self, root: Path, body: str, *, status: str = "closed") -> None:
        d = root / "reports" / PRIOR_ID / "session.md"
        fm = (
            "---\n"
            f"session_id: {PRIOR_ID}\n"
            "role: BE-YAC\n"
            "started_at: 2026-07-01T12:00:00\n"
            f"status: {status}\n"
            "---\n"
        )
        d.write_text(fm + body)

    def test_session_that_died_active_reports_stub(self, tmp_path):
        # The dominant case: close_prior writes the placeholder on this run,
        # and the signal must name it so the caller backfills.
        root = make_project(tmp_path, prior_status="active")
        res = run(root, kbc=make_fake_kbc(tmp_path)[0])
        assert res.returncode == 0, res.stderr
        assert out_ids(res)["PRIOR_SUMMARY"] == "stub"

    def test_real_summary_reports_content(self, tmp_path):
        root = make_project(tmp_path, prior_status="closed")
        self._write_body(
            root,
            "\n# Session\n\n## Session Summary\n**Duration:** 10:00 - 12:00\n"
            "**What happened:** Shipped the thing.\n",
        )
        res = run(root, kbc=make_fake_kbc(tmp_path)[0])
        assert res.returncode == 0, res.stderr
        assert out_ids(res)["PRIOR_SUMMARY"] == "content"

    def test_stub_left_by_an_earlier_wake_reports_stub(self, tmp_path):
        root = make_project(tmp_path, prior_status="closed")
        self._write_body(
            root,
            "\n# Session\n\nBody text.\n\n"
            "## Session Summary — auto-closed by /wip-wake (2026-07-01T13:00:00)\n",
        )
        res = run(root, kbc=make_fake_kbc(tmp_path)[0])
        assert res.returncode == 0, res.stderr
        assert out_ids(res)["PRIOR_SUMMARY"] == "stub"

    def test_content_wins_over_a_trailing_empty_heading(self, tmp_path):
        # An active session that had already written a summary gets the
        # placeholder appended anyway; the real one must still win, or the
        # backfill would overwrite a summary someone actually wrote.
        root = make_project(tmp_path, prior_status="active")
        self._write_body(
            root,
            "\n# Session\n\n## Session Summary\nReal content someone wrote.\n",
            status="active",
        )
        res = run(root, kbc=make_fake_kbc(tmp_path)[0])
        assert res.returncode == 0, res.stderr
        assert out_ids(res)["PRIOR_SUMMARY"] == "content"

    def test_no_prior_reports_absent(self, tmp_path):
        root = make_project(tmp_path, with_prior=False)
        res = run(root, "--fresh")
        assert res.returncode == 0, res.stderr
        assert out_ids(res)["PRIOR_SUMMARY"] == "absent"

    def test_fresh_mode_reports_the_closed_prior_it_leaves_behind(self, tmp_path):
        root = make_project(tmp_path, prior_status="closed")
        self._write_body(
            root,
            "\n# Session\n\n"
            "## Session Summary — auto-closed by /wip-wake (2026-07-01T13:00:00)\n",
        )
        res = run(root, "--fresh")
        assert res.returncode == 0, res.stderr
        # --fresh doesn't touch the prior, but a stub is still worth naming.
        assert out_ids(res)["PRIOR_SUMMARY"] == "stub"


class TestDryRun:
    def test_dry_run_writes_nothing(self, tmp_path):
        root = make_project(tmp_path)
        kbc, log = make_fake_kbc(tmp_path)
        before = (root / "reports" / PRIOR_ID / "session.md").read_text()
        res = run(root, "--dry-run", kbc=kbc)
        assert res.returncode == 0, res.stderr
        assert (root / "reports" / PRIOR_ID / "session.md").read_text() == before
        assert (root / ".claude" / ".session-id").read_text().strip() == PRIOR_ID
        assert not (root / "reports" / f"BE-YAC-{FROZEN}").exists()
        assert not log.exists()
        # Still prints the contract lines so agents can preview.
        assert out_ids(res)["NEW_ID"] == f"BE-YAC-{FROZEN}"
        # Reports the plan's outcome — the close it didn't perform would have
        # left a placeholder — rather than the untouched file's state.
        assert out_ids(res)["PRIOR_SUMMARY"] == "stub"
