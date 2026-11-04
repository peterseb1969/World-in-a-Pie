"""An app's production image starts without fetching packages.

`npx <pkg>` downloads <pkg> at every start when the image lacks it, so an
install without npm registry access cannot start the app. The check fails
`npx` without `--no-install`, and a runner that is only a devDependency of an
image whose runtime stage installs production dependencies only. The shapes
below are the app repos' Dockerfiles as they stood when this was written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wip_deploy.check_app import check_runtime_fetches_nothing

BUILD = "FROM node:24-alpine AS build\nWORKDIR /app\nCOPY package.json ./\nRUN npm ci\n"

# WIP-KB before its fix: runtime installs prod deps only, tsx a devDependency.
KB_BEFORE = BUILD + (
    "FROM node:24-alpine AS production\n"
    "RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force\n"
    'CMD ["npx", "tsx", "server/index.ts"]\n'
)
KB_AFTER = KB_BEFORE.replace('"npx", "tsx"', '"npx", "--no-install", "tsx"')
# WIP-RC: prod-only install, runner by path.
RC = KB_BEFORE.replace('"npx", "tsx"', '"./node_modules/.bin/tsx"')
# The query scaffold / WIP-Cooking: the runtime copies the full node_modules.
FULL_COPY = BUILD + (
    "FROM node:24-slim AS runtime\n"
    "COPY --from=build /app/node_modules ./node_modules\n"
    "HEALTHCHECK --interval=30s \\\n"
    "  CMD node -e \"fetch('http://localhost:3001/api/health')\"\n"
    "CMD {cmd}\n"
)


def _app(tmp_path: Path, dockerfile: str, *, deps=(), dev_deps=()) -> Path:
    (tmp_path / "Dockerfile").write_text(dockerfile)
    (tmp_path / "package.json").write_text(json.dumps({
        "dependencies": dict.fromkeys(deps, "^1"),
        "devDependencies": dict.fromkeys(dev_deps, "^1"),
    }))
    return tmp_path


def test_kb_before_its_fix_fails(tmp_path):
    r = check_runtime_fetches_nothing(_app(tmp_path, KB_BEFORE, dev_deps=["tsx"]))
    assert not r.passed
    assert "--no-install" in r.message


def test_a_dev_only_runner_under_a_prod_only_install_fails_even_with_no_install(tmp_path):
    r = check_runtime_fetches_nothing(_app(tmp_path, KB_AFTER, dev_deps=["tsx"]))
    assert not r.passed
    assert "devDependency" in r.message and "dependencies" in r.fix_hint


def test_kb_after_its_fix_passes(tmp_path):
    r = check_runtime_fetches_nothing(_app(tmp_path, KB_AFTER, deps=["tsx"]))
    assert r.passed, r.message


def test_rc_by_path_passes(tmp_path):
    r = check_runtime_fetches_nothing(_app(tmp_path, RC, deps=["tsx"]))
    assert r.passed, r.message


def test_a_dev_only_runner_by_path_under_a_prod_only_install_fails(tmp_path):
    r = check_runtime_fetches_nothing(_app(tmp_path, RC, dev_deps=["tsx"]))
    assert not r.passed


@pytest.mark.parametrize("deps,dev_deps", [(["tsx"], []), ([], ["tsx"])])
def test_npx_without_no_install_fails_whatever_the_image_holds(tmp_path, deps, dev_deps):
    """WIP-AA (tsx a dependency) and WIP-Cooking (a devDependency, full copy)
    both run today; the missing guard is what fails."""
    dockerfile = FULL_COPY.format(cmd='["npx", "tsx", "server/index.ts"]')
    r = check_runtime_fetches_nothing(_app(tmp_path, dockerfile, deps=deps, dev_deps=dev_deps))
    assert not r.passed
    assert "./node_modules/.bin/tsx" in r.fix_hint


def test_a_full_node_modules_copy_carries_dev_dependencies(tmp_path):
    dockerfile = FULL_COPY.format(cmd='["npx", "--no-install", "tsx", "server/index.ts"]')
    r = check_runtime_fetches_nothing(_app(tmp_path, dockerfile, dev_deps=["tsx"]))
    assert r.passed, r.message


def test_the_healthcheck_cmd_is_not_the_start_command(tmp_path):
    dockerfile = FULL_COPY.format(cmd='["node", "dist/server.js"]')
    r = check_runtime_fetches_nothing(_app(tmp_path, dockerfile))
    assert r.passed
    assert "runs no npx" in r.message


def test_shell_form_is_read(tmp_path):
    dockerfile = BUILD + "FROM node:24-alpine\nCMD npx tsx server/index.ts\n"
    r = check_runtime_fetches_nothing(_app(tmp_path, dockerfile, deps=["tsx"]))
    assert not r.passed


def test_no_production_dockerfile_is_not_this_checks_concern(tmp_path):
    assert check_runtime_fetches_nothing(tmp_path).passed


def test_the_query_scaffold_passes():
    """The scaffold the query preset copies from scripts/scaffold-query."""
    root = Path(__file__).resolve().parents[3] / "scripts" / "scaffold-query"
    r = check_runtime_fetches_nothing(root)
    assert r.passed, r.message
