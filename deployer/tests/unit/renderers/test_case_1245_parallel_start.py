"""CASE-1245 item 3 — nothing in the render path may read as a start-order promise.

`docs/design/wip-deploy-v2.md` said the compose renderer emits activation-aware
`depends_on`, with `service_healthy` for deps that have a healthcheck and
`service_started` for those that don't — in the per-target notes and again in a
"Dependency ordering" section. The helper that supposedly produced it returned `{}`
unconditionally, and both renderers computed `active_names` / `healthcheck_owners`
solely to hand them to it.

Parallel start is the real decision: every service retries its own dependencies,
so the gate only lengthened cold boot. The documentation was the bug, and the
plumbing that existed only to filter an empty result went with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import compose as compose_renderer
from wip_deploy.renderers import dev_simple as dev_renderer
from wip_deploy.renderers import render_dev_simple

from .test_dev_simple import _dev_deployment, _secrets

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()
DESIGN_DOC = REPO_ROOT / "docs" / "design" / "wip-deploy-v2.md"


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


class TestDevRendererStartsInParallel:
    def test_no_service_gets_depends_on(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """`test_compose.py::test_no_depends_on_emitted` pins the same rule for
        the compose target; the dev renderer shares the helpers, so it needed its
        own."""
        d = _dev_deployment()
        tree = render_dev_simple(
            d,
            real_discovery.components,
            real_discovery.apps,
            _secrets(tmp_path, d, real_discovery),
            repo_root=REPO_ROOT,
        )
        doc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
        for name, svc in doc["services"].items():
            assert "depends_on" not in svc, (
                f"Service {name!r} still has depends_on: {svc['depends_on']}"
            )


class TestDeadPlumbingRemoved:
    @pytest.mark.parametrize("mod", [compose_renderer, dev_renderer])
    def test_the_helper_that_always_returned_empty_is_gone(self, mod: object) -> None:
        assert not hasattr(mod, "_depends_on_block")

    @pytest.mark.parametrize("mod", [compose_renderer, dev_renderer])
    def test_the_sets_computed_only_for_that_helper_are_gone(self, mod: object) -> None:
        """`active_names` and `healthcheck_owners` distinguished service_healthy
        from service_started for a block that was never emitted — leaving them
        kept the false contract alive in the code a reader trusts."""
        src = Path(mod.__file__).read_text()  # type: ignore[attr-defined]
        assert "active_names" not in src
        assert "healthcheck_owners" not in src
        assert 'block["depends_on"]' not in src


class TestDesignDocMatchesTheDecision:
    @pytest.mark.parametrize(
        "phrase",
        [
            "activation-aware",
            "emits `depends_on",
            "condition: service_healthy",
            "service_started",
        ],
    )
    def test_the_doc_makes_no_emission_promise(self, phrase: str) -> None:
        """Naming the flag to say it is never emitted is fine; promising the
        gates is what let a reader design against ordering that does not exist."""
        assert phrase not in DESIGN_DOC.read_text()
