"""CASE-879 — `rotate-key` rotates the master key too, names what restarts, and checks.

The install's master key (`api-key`, read by every service and app) had no
rotate command: `rotate-key` accepted only spec-declared keys. Its closing
message also claimed consumers "pick up the new value automatically", which
was false on k8s (subPath mounts, env read at start) and for every service
that validates keys from api-keys.json.

Contract under test: `--secret api-key` rotates the master key (other
generated secrets are refused), the message names the services the apply
restarts, and the post-apply probe fails the command unless the new key is
accepted and the old one refused.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app
from wip_deploy.verify_security import check_key_rotation

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def _install(tmp_path: Path, api_keys: list[dict] | None = None) -> Path:
    d = build_deployment(
        BuildInputs(
            target="compose",
            variant="dev",
            hostname="localhost",
            tls="internal",
            compose_data_dir=tmp_path / "data",
            api_keys=api_keys or [],
        )
    )
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    d = d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    (tmp_path / "secrets").mkdir(exist_ok=True)
    return tmp_path


def _rotate(install: Path, *args: str):
    return runner.invoke(app, [
        "rotate-key", *args, "--install-dir", str(install), "--repo-root", str(REPO_ROOT),
    ])


def _regenerate(secret_file: Path, value: str):
    """Stands in for the apply: ensure_secrets regenerates the removed secret."""
    def apply(*_a, **_kw):
        assert not secret_file.exists(), "the old secret must be removed before the apply"
        secret_file.write_text(value)
    return apply


class TestArguments:
    def test_neither_name_nor_secret_is_refused(self, tmp_path):
        result = _rotate(_install(tmp_path))
        assert result.exit_code == 2
        assert "exactly one" in result.output

    def test_both_name_and_secret_is_refused(self, tmp_path):
        result = _rotate(_install(tmp_path), "web-yac", "--secret", "api-key")
        assert result.exit_code == 2

    def test_other_generated_secrets_are_refused_untouched(self, tmp_path):
        install = _install(tmp_path)
        pw = install / "secrets" / "postgres-password"
        pw.write_text("keep-me")
        with patch("wip_deploy.cli._apply_and_persist_mutation") as apply:
            result = _rotate(install, "--secret", "postgres-password")
        assert result.exit_code == 2
        assert "cannot be rotated" in result.output
        assert pw.read_text() == "keep-me"
        apply.assert_not_called()

    def test_unknown_declared_key_points_at_secret_flag(self, tmp_path):
        result = _rotate(_install(tmp_path), "no-such-key")
        assert result.exit_code == 2
        assert "--secret api-key" in result.output


class TestMasterKey:
    def test_rotates_names_readers_and_checks(self, tmp_path):
        install = _install(tmp_path)
        key = install / "secrets" / "api-key"
        key.write_text("old-master")
        with (
            patch("wip_deploy.cli._apply_and_persist_mutation",
                  side_effect=_regenerate(key, "new-master")),
            patch("wip_deploy.verify_security.check_key_rotation",
                  return_value=[]) as check,
        ):
            result = _rotate(install, "--secret", "api-key")

        assert result.exit_code == 0, result.output
        # The plaintext stays out of the output by default (it once leaked into
        # an agent transcript); only the file holding it is named.
        assert "new-master" not in result.output
        assert f"secret file: {key}" in result.output
        readers_line = next(line for line in result.output.splitlines()
                            if line.startswith("Restarted by this apply"))
        assert "registry" in readers_line and "document-store" in readers_line
        assert "mongodb" not in readers_line  # storage-bearing: never restarted this way
        assert check.call_args.args[1:] == ("new-master", "old-master")
        assert "✓ post-rotation check" in result.output

    def test_show_key_prints_the_plaintext(self, tmp_path):
        install = _install(tmp_path)
        key = install / "secrets" / "api-key"
        key.write_text("old-master")
        with (
            patch("wip_deploy.cli._apply_and_persist_mutation",
                  side_effect=_regenerate(key, "new-master")),
            patch("wip_deploy.verify_security.check_key_rotation", return_value=[]),
        ):
            result = _rotate(install, "--secret", "api-key", "--show-key")

        assert result.exit_code == 0, result.output
        assert "new-master" in result.output
        assert "old-master" not in result.output

    def test_failed_check_fails_the_command(self, tmp_path):
        install = _install(tmp_path)
        key = install / "secrets" / "api-key"
        key.write_text("old-master")
        with (
            patch("wip_deploy.cli._apply_and_persist_mutation",
                  side_effect=_regenerate(key, "new-master")),
            patch("wip_deploy.verify_security.check_key_rotation",
                  return_value=["the old key is still answered 200"]),
        ):
            result = _rotate(install, "--secret", "api-key")

        assert result.exit_code == 1
        assert "post-rotation check failed" in result.output
        assert "still answered 200" in result.output

    def test_no_check_skips_the_probe(self, tmp_path):
        install = _install(tmp_path)
        key = install / "secrets" / "api-key"
        key.write_text("old-master")
        with (
            patch("wip_deploy.cli._apply_and_persist_mutation",
                  side_effect=_regenerate(key, "new-master")),
            patch("wip_deploy.verify_security.check_key_rotation") as check,
        ):
            result = _rotate(install, "--secret", "api-key", "--no-check")

        assert result.exit_code == 0, result.output
        check.assert_not_called()


class TestDeclaredKey:
    def test_declared_key_restarts_nothing_and_says_so(self, tmp_path):
        install = _install(tmp_path, [{"name": "agent", "namespaces": ["wip"]}])
        key = install / "secrets" / "agent-api-key"
        key.write_text("old-agent")
        with (
            patch("wip_deploy.cli._apply_and_persist_mutation",
                  side_effect=_regenerate(key, "new-agent")),
            patch("wip_deploy.verify_security.check_key_rotation", return_value=[]),
        ):
            result = _rotate(install, "agent")

        assert result.exit_code == 0, result.output
        # The services that check declared keys re-read api-keys.json while
        # running (CASE-884): nobody reads this key from the environment, so
        # nothing restarts, and the output says how the key arrives instead.
        assert not any(line.startswith("Restarted by this apply")
                       for line in result.output.splitlines())
        assert "re-read api-keys.json" in result.output
        assert "new-agent" not in result.output


class TestProbe:
    def _deployment(self, tmp_path):
        from wip_deploy.cli import _load_deployment
        return _load_deployment(_install(tmp_path))

    def _transport(self, accepted: set[str]):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/registry/namespaces"
            return httpx.Response(200 if request.headers.get("X-API-Key") in accepted else 401)
        return httpx.MockTransport(handler)

    def test_new_accepted_old_refused_is_clean(self, tmp_path):
        d = self._deployment(tmp_path)
        assert check_key_rotation(d, "new", "old", transport=self._transport({"new"})) == []

    def test_new_key_refused_is_reported(self, tmp_path):
        d = self._deployment(tmp_path)
        problems = check_key_rotation(
            d, "new", "old", transport=self._transport(set()), attempts=2, delay_seconds=0,
        )
        assert len(problems) == 1 and "new key is not accepted" in problems[0]

    def test_old_key_still_accepted_is_reported(self, tmp_path):
        d = self._deployment(tmp_path)
        problems = check_key_rotation(
            d, "new", "old", transport=self._transport({"new", "old"}),
        )
        assert len(problems) == 1 and "old key is still answered 200" in problems[0]
