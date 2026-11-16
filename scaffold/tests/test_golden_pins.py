"""The golden fixtures name every library an app gets, never its version.

An app scaffold vendors three npm tarballs (@wip/client, @wip/react,
@wip/proxy) and two wheels (wip-archive, wip-toolkit) into the app's libs/.
Their versions are computed from git when the scaffold builds them
(scripts/lib_artifacts.py) and move with every commit to a library, so the
golden harness records each name with the version replaced by a placeholder
(golden_lib.normalize_lib_artifact). A library change then leaves the
fixtures alone; a change to WHICH libraries an app gets still shows.

The golden tests themselves are opt-in (WIP_GOLDEN=1), so CI never runs
them. This test runs everywhere: pure file reads, no scaffold run. It fails
when a fixture pins a concrete library version (captured before the
placeholder, or by a harness that lost the normalization), and when an app
fixture is missing one of the five libraries.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from golden_lib import LIB_VERSION, normalize_lib_artifact

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
FIXTURES = sorted(GOLDEN_DIR.glob("*.json"))
APP_FIXTURES = [f for f in FIXTURES if "tree" in json.loads(f.read_text())]

EXPECTED = {
    f"libs/wip-client-{LIB_VERSION}.tgz",
    f"libs/wip-react-{LIB_VERSION}.tgz",
    f"libs/wip-proxy-{LIB_VERSION}.tgz",
    f"libs/wip_archive-{LIB_VERSION}-py3-none-any.whl",
    f"libs/wip_toolkit-{LIB_VERSION}-py3-none-any.whl",
}

RECAPTURE = "WIP_GOLDEN=1 WIP_GOLDEN_UPDATE=1 ./scripts/wip-test.sh scaffold"


def _artefacts(fixture: Path) -> list[str]:
    return [
        entry
        for entry in json.loads(fixture.read_text())["tree"]
        if entry.startswith("libs/") and entry.endswith((".tgz", ".whl"))
    ]


def test_app_fixtures_exist():
    # Guards the parametrized test: with no app fixture it would pass vacuously.
    assert APP_FIXTURES, "no golden fixture records an app tree — has the fixture format changed?"


@pytest.mark.parametrize("fixture", APP_FIXTURES, ids=lambda p: p.stem)
def test_fixture_names_every_library_without_its_version(fixture: Path):
    found = _artefacts(fixture)
    assert set(found) == EXPECTED and len(found) == len(EXPECTED), (
        f"{fixture.name} names these vendored libraries: {sorted(found)}; expected "
        f"exactly {sorted(EXPECTED)}. A concrete version means the fixture was captured "
        f"without the harness's normalization; re-capture with: {RECAPTURE}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "libs/wip-client-0.108.0.tgz",
        "libs/wip-react-0.26.15+dirty.03793aa7.tgz",
        "libs/wip_archive-0.39.0-py3-none-any.whl",
        "libs/wip_toolkit-0.27.1+local.ab12cd34-py3-none-any.whl",
    ],
)
def test_every_computed_version_shape_is_normalized(name: str):
    assert normalize_lib_artifact(name) in EXPECTED


def test_other_files_keep_their_names():
    for name in ("libs/wip-client-README.md", "src/wip-client-1.0.0.tgz", "libs/other-1.0.0.tgz"):
        assert normalize_lib_artifact(name) == name
    assert not re.search(r"\d", LIB_VERSION)


def _lib_artifacts():
    """scripts/lib_artifacts.py loaded as a module — it is a script, not an
    installed package."""
    import importlib.util

    from golden_lib import REPO_ROOT

    spec = importlib.util.spec_from_file_location("lib_artifacts", REPO_ROOT / "scripts/lib_artifacts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["lib_artifacts"] = module
    spec.loader.exec_module(module)
    return module


def test_the_build_cache_variable_is_the_one_lib_artifacts_reads():
    """The golden session hands lib_artifacts.py its build cache through an
    environment variable; a harness that set another name would rebuild every
    library on every run again, silently."""
    from golden_lib import BUILD_CACHE_ENV

    assert _lib_artifacts().BUILD_CACHE_ENV == BUILD_CACHE_ENV


def test_fresh_rebuild_lifts_the_build_cache_for_the_second_run(monkeypatch, tmp_path):
    """A cached session hands a refresh the artifact the create run built, so
    the surfaces' immutability check (same artifact name, different bytes is
    fatal) would compare a copy against itself. `fresh_rebuild` lifts the cache
    for the second run only, and the empty value is what lib_artifacts.py reads
    as no cache — the same spelling `_build_cache` already treats as unset."""
    import subprocess

    import golden_lib as G

    module = _lib_artifacts()
    seen: list[dict[str, str] | None] = []

    def fake_run(cmd, cwd, env_extra=None):
        seen.append(env_extra)
        return subprocess.CompletedProcess(cmd, 0, "Refreshing\n", "")

    monkeypatch.setattr(G, "_run", fake_run)
    monkeypatch.setattr(G, "snapshot_tree", lambda root, roots: {"tree": [], "files": {}})

    def run(refresh: bool, fresh_rebuild: bool) -> None:
        G.run_app_scenario(
            tmp_path, preset="standard", tier3=False, refresh=refresh,
            slug="pins", fresh_rebuild=fresh_rebuild,
        )

    run(refresh=True, fresh_rebuild=True)
    assert seen == [None, {module.BUILD_CACHE_ENV: ""}]
    # Only the one scenario pays for a real build; the others keep reusing.
    seen.clear()
    run(refresh=True, fresh_rebuild=False)
    assert seen == [None, None]
    monkeypatch.setenv(module.BUILD_CACHE_ENV, "")
    assert module._build_cache() is None
