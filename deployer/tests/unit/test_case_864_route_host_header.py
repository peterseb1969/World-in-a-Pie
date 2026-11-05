"""Regression coverage for CASE-864 — the `upstream_host_header` route
primitive.

SigV4 presigned URLs sign the Host of the INTERNAL endpoint services sign
against (e.g. wip-garage:3900); the browser presents the public host. A
store that validates the signed Host (Garage; MinIO under SigV4) rejects
the mismatch unless the edge rewrites Host to the upstream. The primitive
is declared on the Route, plumbed through ResolvedRoute/IngressRule, and
honored by BOTH renderers — never hacked into one (CLAUDE.md §6).
"""

from __future__ import annotations

from pathlib import Path

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen import generate_caddy_config
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.spec import Deployment
from wip_deploy.spec.component import (
    Component,
    ComponentMetadata,
    ComponentSpec,
    ImageRef,
    Port,
    Route,
)


def _storage_component(*, host_header: bool) -> Component:
    return Component(
        api_version="wip.dev/v1",
        kind="Component",
        metadata=ComponentMetadata(
            name="blobstore", category="optional", description="synthetic store",
        ),
        spec=ComponentSpec(
            image=ImageRef(name="example/blobstore", tag="v1"),
            ports=[Port(name="s3", container_port=3900)],
            routes=[Route(
                path="/blobstore",
                auth_required=False,
                strip_prefix=True,
                upstream_host_header=host_header,
            )],
        ),
    )


def _deployment(target: str = "compose") -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,
        hostname="wip.local",
        tls="internal",
        compose_data_dir=Path("/tmp/d"),
        add=["blobstore"],
        # No gateway: the synthetic component list carries no auth-gateway.
        auth_mode="api-key-only",
        auth_gateway=False,
    ))
    return d


class TestCaddyRenderer:
    def test_flag_renders_header_up_host(self) -> None:
        d = _deployment()
        cfg = generate_caddy_config(d, [_storage_component(host_header=True)], [])
        text = render_caddyfile(cfg)
        block = text[text.index("handle_path /blobstore/*"):]
        block = block[: block.index("\n    }") + 6]
        assert "header_up Host {upstream_hostport}" in block

    def test_without_flag_no_header_up(self) -> None:
        d = _deployment()
        cfg = generate_caddy_config(d, [_storage_component(host_header=False)], [])
        text = render_caddyfile(cfg)
        assert "header_up Host" not in text


class TestK8sRenderer:
    """On k8s the front door is wip-caddy (CASE-1067), so the flag is the
    same Caddy `header_up Host {upstream_hostport}` as on compose. The
    upstream must be dialled at the Service FQDN: that is the host the env
    layer hands services, and a SigV4 validator rejects any other spelling
    (Garage answered every presigned download "AccessDenied: Invalid
    signature" with the short name, observed live on dev-test, 2026-10-18)."""

    def _block(self, *, host_header: bool) -> str:
        d = _deployment(target="k8s")
        text = render_caddyfile(generate_caddy_config(d, [_storage_component(host_header=host_header)], []))
        block = text[text.index("handle_path /blobstore/*"):]
        return block[: block.index("\n    }") + 6]

    def test_flag_sets_the_upstream_host(self) -> None:
        block = self._block(host_header=True)
        assert "header_up Host {upstream_hostport}" in block
        assert "reverse_proxy wip-blobstore.wip.svc.cluster.local:3900 {" in block

    def test_without_flag_no_host_rewrite(self) -> None:
        assert "header_up Host" not in self._block(host_header=False)


def test_flag_preserved_across_targets() -> None:
    comp = _storage_component(host_header=True)
    for target in ("compose", "k8s"):
        caddy = generate_caddy_config(_deployment(target=target), [comp], [])
        assert next(r for r in caddy.routes if r.path == "/blobstore").upstream_host_header


def test_k8s_upstream_host_matches_env_component_host() -> None:
    """The dialled upstream (which `{upstream_hostport}` names) and the env
    resolver must render the SAME hostport. document-store signs presigned
    URLs against the endpoint the env layer hands it; SigV4 signs the Host,
    so this pin fails if either side changes its host form unilaterally."""
    from wip_deploy.config_gen.env import _component_host

    cfg = generate_caddy_config(_deployment(target="k8s"), [_storage_component(host_header=True)], [])
    assert cfg.backend("blobstore", 3900) == f"{_component_host('blobstore', 'k8s', namespace='wip')}:3900"
