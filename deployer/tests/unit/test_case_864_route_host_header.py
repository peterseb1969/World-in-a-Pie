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

import yaml

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen import generate_caddy_config, generate_ingress_config
from wip_deploy.renderers.compose_caddy import render_caddyfile
from wip_deploy.renderers.k8s import _render_ingress
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
    def _docs(self, *, host_header: bool) -> list[dict]:
        d = _deployment(target="k8s")
        ing = generate_ingress_config(
            d, [_storage_component(host_header=host_header)], []
        )
        return list(yaml.safe_load_all(_render_ingress(ing, "wip")))

    def test_flag_sets_upstream_vhost_annotation(self) -> None:
        # The restored Host must be the FQDN form — the exact string the
        # env layer hands services as the component URL host on k8s
        # (config_gen/env.py _component_host). The short service name is
        # a DIFFERENT host to a SigV4 validator: with it, Garage answered
        # every presigned download "AccessDenied: Invalid signature"
        # (observed live on dev-test, 2026-10-18).
        docs = self._docs(host_header=True)
        ing = next(d for d in docs if d["metadata"]["name"] == "blobstore-ingress")
        assert ing["metadata"]["annotations"][
            "nginx.ingress.kubernetes.io/upstream-vhost"
        ] == "wip-blobstore.wip.svc.cluster.local:3900"
        # strip_prefix semantics are untouched by the new flag.
        assert ing["metadata"]["annotations"][
            "nginx.ingress.kubernetes.io/rewrite-target"
        ] == "/$2"

    def test_without_flag_no_annotation(self) -> None:
        docs = self._docs(host_header=False)
        ing = next(d for d in docs if d["metadata"]["name"] == "blobstore-ingress")
        assert (
            "nginx.ingress.kubernetes.io/upstream-vhost"
            not in ing["metadata"]["annotations"]
        )


def test_flag_preserved_across_targets() -> None:
    comp = _storage_component(host_header=True)
    caddy = generate_caddy_config(_deployment(), [comp], [])
    ing = generate_ingress_config(_deployment(target="k8s"), [comp], [])
    assert next(r for r in caddy.routes if r.path == "/blobstore").upstream_host_header
    assert next(r for r in ing.rules if r.path == "/blobstore").upstream_host_header


def test_k8s_upstream_vhost_matches_env_component_host() -> None:
    """The annotation and the env resolver must render the SAME hostport.

    document-store signs presigned URLs against the endpoint the env layer
    hands it; the ingress restores Host from this annotation. SigV4 signs
    the Host, so the two renderings of "the upstream" must be
    byte-identical — this pin fails if either side changes its host form
    unilaterally.
    """
    from wip_deploy.config_gen.env import _component_host

    d = _deployment(target="k8s")
    ing = generate_ingress_config(d, [_storage_component(host_header=True)], [])
    docs = list(yaml.safe_load_all(_render_ingress(ing, "wip")))
    blob = next(doc for doc in docs if doc["metadata"]["name"] == "blobstore-ingress")
    vhost = blob["metadata"]["annotations"][
        "nginx.ingress.kubernetes.io/upstream-vhost"
    ]
    assert vhost == f"{_component_host('blobstore', 'k8s', namespace='wip')}:3900"
