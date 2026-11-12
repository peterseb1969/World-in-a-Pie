"""CASE-1245 item 1 — `--data-dir` is refused, because no target renders it.

The flag was accepted, defaulted, and recorded into `spec.platform.compose.data_dir`,
which no renderer reads: compose storage is named volumes
(`<install>_wip-<component>-<storage>`), placed by the container engine. So
`--data-dir /mnt/ssd` reported a successful install with every byte still on the
default filesystem — and the operator guide built its backup and relocation
procedures on `WIP_DATA_DIR`, an env var the deployer has never written.

Refusal rather than silence: `ComposePlatform.data_dir` stays required (every saved
compose spec carries it, and the spec models forbid unknown keys), so the CLI is the
one place an operator can be told what actually happens.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wip_deploy.cli import app

runner = CliRunner()

DEPLOYER_SRC = Path(__file__).resolve().parents[2] / "src"
REPO_DOCS = Path(__file__).resolve().parents[3] / "docs"

_BASE = [
    "--name", "dd",
    "--hostname", "wip.localhost",
    "--preset", "standard",
    "--target", "compose",
    "--auth-mode", "api-key-only",
    "--no-auth-gateway",
]


class TestDataDirRefused:
    @pytest.mark.parametrize("verb", ["show-spec", "validate"])
    def test_a_verb_that_builds_a_spec_refuses_it_and_names_the_real_storage(
        self, verb: str
    ) -> None:
        result = runner.invoke(app, [verb, *_BASE, "--data-dir", "/mnt/wip-data"])
        assert result.exit_code == 2
        assert "not implemented" in result.output
        # The remedy has to be findable in the refusal itself.
        assert "_wip-mongodb-data" in result.output
        assert "podman volume create" in result.output

    @pytest.mark.parametrize("target", ["compose", "dev", "k8s"])
    def test_refused_on_every_target(self, target: str) -> None:
        result = runner.invoke(
            app,
            [
                "show-spec",
                "--name", "dd",
                "--hostname", "wip.localhost",
                "--target", target,
                "--auth-mode", "api-key-only",
                "--no-auth-gateway",
                "--data-dir", "/mnt/wip-data",
            ],
        )
        assert result.exit_code == 2
        assert "not implemented" in result.output

    def test_install_refuses_before_the_directory_exists(self, tmp_path: Path) -> None:
        target = tmp_path / "never-written"
        result = runner.invoke(
            app,
            ["install", *_BASE, "--install-dir", str(target),
             "--data-dir", "/mnt/wip-data"],
        )
        assert result.exit_code == 2
        assert not target.exists()

    def test_render_refuses_before_the_directory_exists(self, tmp_path: Path) -> None:
        target = tmp_path / "never-rendered"
        result = runner.invoke(
            app, ["render", *_BASE, "--output-dir", str(target),
                 "--data-dir", "/mnt/wip-data"]
        )
        assert result.exit_code == 2
        assert not target.exists()

    def test_help_does_not_promise_a_data_directory(self) -> None:
        result = runner.invoke(app, ["install", "--help"])
        assert result.exit_code == 0
        # Under GITHUB_ACTIONS (which Gitea's runner sets) rich colours the
        # help and styles "--data-dir" in pieces, so match on plain text.
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        block = plain[plain.index("--data-dir") :]
        assert "Not implemented" in block[:300]


class TestSpecRecordIsUnchanged:
    def test_an_omitted_data_dir_still_fills_the_required_field(self) -> None:
        """The model field is required, so a normal compose install must still
        record something — the refusal targets the flag, not the spec shape."""
        result = runner.invoke(app, ["show-spec", *_BASE, "--format", "json"])
        assert result.exit_code == 0, result.output
        dumped = json.loads(result.output)
        data_dir = Path(dumped["spec"]["platform"]["compose"]["data_dir"])
        assert data_dir.name == "data"

    def test_platform_variant_is_still_recorded(self) -> None:
        result = runner.invoke(app, ["show-spec", *_BASE, "--format", "json"])
        assert result.exit_code == 0, result.output
        dumped = json.loads(result.output)
        assert dumped["spec"]["platform"]["compose"]["platform_variant"] == "default"


class TestNoPhantomDataDir:
    def test_the_deployer_never_writes_wip_data_dir(self) -> None:
        """`WIP_DATA_DIR` was documented as the env var behind `--data-dir`.
        Nothing ever wrote it, and an operator following the backup procedure
        archived a path that does not exist."""
        offenders = [
            p.relative_to(DEPLOYER_SRC.parent)
            for p in DEPLOYER_SRC.rglob("*.py")
            if "__pycache__" not in p.parts and "WIP_DATA_DIR" in p.read_text()
        ]
        assert offenders == []

    def test_no_doc_instructs_an_operator_to_pass_it(self) -> None:
        """Mentioning the flag to say it is refused is fine; showing it with a
        path is the fiction this item removes."""
        advice = re.compile(r"--data-dir\s+(/|~|<)")
        offenders = [
            p.relative_to(REPO_DOCS.parent)
            for p in REPO_DOCS.rglob("*.md")
            if advice.search(p.read_text())
        ]
        assert offenders == []
