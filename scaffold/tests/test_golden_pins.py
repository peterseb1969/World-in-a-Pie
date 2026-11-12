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
