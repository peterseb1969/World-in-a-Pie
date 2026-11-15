"""The gateway's rate limit reaches the client through wip-caddy.

auth-gateway limits /auth/verify per client address, from X-Forwarded-For,
and its 429 carries Retry-After. Both depend on how Caddy's forward_auth
treats the subrequest, checked against Caddy 2.11.7 (the `caddy:2` image of
2026-11) with a gateway stand-in answering 429: the subrequest carries the
client's address in X-Forwarded-For, and an answer that no `handle_response`
matches is copied back to the client, status and headers included.

What the render controls is the second half: a forward_auth block may turn
only the 401 into the login redirect. A `handle_response` matching any other
status, or no matcher at all, would swallow the 429. And no block may drop
X-Forwarded-For from the subrequest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen.caddy import generate_caddy_config
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.compose_caddy import render_caddyfile

from .test_compose import REPO_ROOT


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _blocks(text: str) -> list[str]:
    """Each forward_auth block's body."""
    out = []
    for m in re.finditer(r"^(\s*)forward_auth [^\n]*\{\n", text, re.MULTILINE):
        indent = m.group(1)
        end = text.index(f"\n{indent}}}", m.end())
        out.append(text[m.end():end])
    return out


@pytest.mark.parametrize("target", ["compose", "k8s"])
def test_forward_auth_rewrites_only_the_401(discovery, target):
    extra = {"k8s_namespace": "wip"} if target == "k8s" else {"compose_data_dir": Path("/tmp/d")}
    d = build_deployment(BuildInputs(
        target=target, hostname="wip.example", preset="standard",
        apps=["react-console", "wip-kb"], add=["app-edge"], registry="ghcr.io/x", **extra,
    ))
    blocks = _blocks(render_caddyfile(generate_caddy_config(d, discovery.components, discovery.apps)))
    assert blocks, "no forward_auth rendered"
    for body in blocks:
        matchers = re.findall(r"^\s*(@\S+) status (.+)$", body, re.MULTILINE)
        assert matchers == [("@unauth", "401")], body
        assert re.findall(r"handle_response(?: (\S+))? \{", body) == ["@unauth"], body
        assert "-X-Forwarded-For" not in body, body
