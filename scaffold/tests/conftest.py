"""Gate for the golden-snapshot tests.

Golden runs execute the real scaffold scripts against scratch clones/dirs.
They need a very specific local environment (see golden_lib docstring), so
they are OPT-IN: set WIP_GOLDEN=1 to run them. Everything else in this
package (the step-3 engine unit tests, when they land) runs unconditionally
— only tests marked `golden` are gated, so the future CI job runs the
engine suite without any env flag.

Capture / re-capture fixtures deliberately:

    WIP_GOLDEN=1 WIP_GOLDEN_UPDATE=1 ./scripts/wip-test.sh scaffold

then review the golden/*.json diff like any code change.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "golden: runs the real scaffold scripts; opt-in via WIP_GOLDEN=1"
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WIP_GOLDEN") == "1":
        _check_prereqs()
        return
    skip = pytest.mark.skip(
        reason="golden runs are opt-in: WIP_GOLDEN=1 (see scaffold/tests/conftest.py)"
    )
    for item in items:
        if "golden" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def lib_build_cache():
    """Build the five vendored libraries once per golden session.

    Every app scaffold run builds them, and their inputs cannot change while
    the session runs, so the scripts the golden tests run reuse one build
    through lib_artifacts.py's cache (keyed on each package's computed
    version). The directory lives only as long as the session: the cache
    does not cover the toolchain, so it must never outlast one run."""
    if os.environ.get("WIP_GOLDEN") != "1":
        yield
        return
    from golden_lib import BUILD_CACHE_ENV

    cache = tempfile.mkdtemp(prefix="wip-golden-libs-")
    previous = os.environ.get(BUILD_CACHE_ENV)
    os.environ[BUILD_CACHE_ENV] = cache
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(BUILD_CACHE_ENV, None)
        else:
            os.environ[BUILD_CACHE_ENV] = previous
        shutil.rmtree(cache, ignore_errors=True)


def _check_prereqs() -> None:
    # (The BSD-sed requirement is gone: the query-preset migration removed
    # every `sed -i` from create-app-project.sh, so golden runs no longer
    # depend on a sed flavor.)
    if shutil.which("podman") is None:
        pytest.exit("WIP_GOLDEN=1 requires podman (running-install key detection)", 1)
    from golden_lib import missing_venv_modules

    missing = missing_venv_modules()
    if missing:
        pytest.exit(
            "WIP_GOLDEN=1 requires a provisioned .venv; it cannot import "
            f"{', '.join(missing)}. A golden run installs nothing into it — run "
            "scripts/setup-backend-agent.sh from this clone first.", 1,
        )
