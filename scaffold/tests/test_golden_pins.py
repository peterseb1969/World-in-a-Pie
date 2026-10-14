"""The golden fixtures pin the version of every library artefact an app gets.

An app scaffold copies the vendored client-lib tarballs
(libs/wip-<lib>/wip-<lib>-<version>.tgz) and the toolkit/archive wheels into
the app's libs/, so every app golden fixture names those files, version and
all. A library version bump therefore changes the scaffold's output, and the
fixtures must be re-captured in the same change.

The golden tests themselves are opt-in (WIP_GOLDEN=1: podman, a running
install, BSD sed), so CI never runs them and a stale fixture stays invisible
until the next agent runs them locally and finds drift it did not cause.
That happened on three consecutive @wip/client bumps, even though the bump
checklist in the backend CLAUDE.md names the re-capture as its fourth step.

This test closes the gap mechanically and runs everywhere: pure file reads,
no podman, no scaffold run. It compares the versions the fixtures pin with
the versions the sources declare (each lib's package.json, each wheel's
pyproject.toml [project] table). It deliberately avoids tomllib, because the
CI runner's system python may predate 3.11.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
FIXTURES = sorted(GOLDEN_DIR.glob("*.json"))

NPM_LIBS = ("client", "react", "proxy")
# Wheel distribution name (as it appears in the wheel filename) -> the
# pyproject.toml that declares its version. A wheel missing here fails the
# test, so a newly vendored artefact cannot slip past unchecked.
WHEEL_SOURCES = {
    "wip_archive": "libs/wip-archive/pyproject.toml",
    "wip_toolkit": "WIP-Toolkit/pyproject.toml",
}

RECAPTURE = "WIP_GOLDEN=1 WIP_GOLDEN_UPDATE=1 ./scripts/wip-test.sh scaffold"

_TARBALL = re.compile(r"^libs/wip-(?P<lib>[a-z]+)-(?P<version>[^/]+)\.tgz$")
_WHEEL = re.compile(r"^libs/(?P<name>[A-Za-z0-9_]+)-(?P<version>[^-/]+)-[^/]+\.whl$")


def _pyproject_version(path: Path) -> str:
    """The `version` key of the [project] table, without a TOML parser."""
    in_project = False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = re.match(r'version\s*=\s*"([^"]+)"', stripped)
            if m:
                return m.group(1)
    raise AssertionError(f"{path}: no version in its [project] table")


def _declared_versions() -> dict[str, str]:
    declared: dict[str, str] = {}
    for lib in NPM_LIBS:
        package_json = REPO_ROOT / "libs" / f"wip-{lib}" / "package.json"
        declared[f"wip-{lib}"] = json.loads(package_json.read_text())["version"]
    for name, rel in WHEEL_SOURCES.items():
        declared[name] = _pyproject_version(REPO_ROOT / rel)
    return declared


def _pinned_artefacts(fixture: Path) -> dict[str, str]:
    """Artefact name -> version, for every tarball and wheel in the fixture's tree.

    Backend fixtures record no `tree`: a backend scaffold vendors nothing.
    """
    pinned: dict[str, str] = {}
    for entry in json.loads(fixture.read_text()).get("tree", []):
        if not entry.startswith("libs/"):
            continue
        if m := _TARBALL.match(entry):
            pinned[f"wip-{m['lib']}"] = m["version"]
        elif m := _WHEEL.match(entry):
            pinned[m["name"]] = m["version"]
        elif entry.endswith((".tgz", ".whl")):
            pinned[entry] = "<unrecognised artefact name>"
    return pinned


def test_app_fixtures_pin_artefacts():
    # Guards the parsing below: if no fixture yields an artefact, the
    # per-fixture test would pass vacuously.
    assert any(_pinned_artefacts(f) for f in FIXTURES), (
        "no golden fixture names a libs/ tarball or wheel — has the fixture "
        "format or the vendoring layout changed?"
    )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_fixture_pins_the_declared_versions(fixture: Path):
    declared = _declared_versions()
    stale = {
        name: (version, declared.get(name, "<no version source mapped>"))
        for name, version in _pinned_artefacts(fixture).items()
        if declared.get(name) != version
    }
    assert not stale, (
        f"{fixture.name} pins library versions that no longer match their "
        f"sources (artefact: fixture -> declared): {stale}. A library was "
        f"bumped without re-capturing the scaffold goldens; run: {RECAPTURE}"
    )
