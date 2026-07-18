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


def _check_prereqs() -> None:
    # (The BSD-sed requirement is gone: the query-preset migration removed
    # every `sed -i` from create-app-project.sh, so golden runs no longer
    # depend on a sed flavor.)
    if shutil.which("podman") is None:
        pytest.exit("WIP_GOLDEN=1 requires podman (running-install key detection)", 1)
