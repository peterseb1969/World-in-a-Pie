"""Every upstream a rendered Caddyfile names is a service the same render produced.

Caddy does not resolve a static upstream when it loads its config: a
`reverse_proxy wip-auth-gateway:4180` with no such host anywhere validates,
renders, applies, and fails only at request time, with a 502 on the paths it
serves. Upstreams and services are also decided in different places — upstreams
from the spec's intent (routes, `auth.gateway`), services from activation — so
a directive whose condition drifts from its service's activation produces such
a Caddyfile with no signal at all.

`check_caddy_upstreams` closes that class at render time, on every target. The
Caddyfiles are found in the tree wherever they land (a `Caddyfile` file on
compose and dev, a ConfigMap's `Caddyfile` key on k8s); the directives that name
an upstream are read by keyword (`reverse_proxy`, `forward_auth`, and `ask`,
whose dead host silently stops on-demand certificates); and the hosts allowed
are the ones the tree itself renders — compose service keys, container names and
network aliases, or k8s Service names in each in-cluster spelling. Nothing is
enumerated by hand, so a new component needs no entry here.

The check is host-only: a wrong port gives the same 502 and is not caught.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from wip_deploy.renderers.base import FileTree

_UPSTREAM_DIRECTIVES = ("reverse_proxy", "forward_auth", "ask")


class UpstreamNotRendered(ValueError):
    """A rendered Caddyfile names an upstream no rendered service provides."""


def check_caddy_upstreams(tree: FileTree) -> None:
    """Raise UpstreamNotRendered naming every upstream in the tree's
    Caddyfiles that no service of the same tree provides."""
    hosts = _rendered_hosts(tree)
    problems = [
        f"{where}, in `{block}`: {directive} {upstream} — no rendered service is named "
        f"{_host_of(upstream)!r}"
        for where, text in _caddyfiles(tree)
        for block, directive, upstream in _upstreams(text)
        if _host_of(upstream) not in hosts
    ]
    if problems:
        raise UpstreamNotRendered(
            "the render routes to services it did not produce:\n  " + "\n  ".join(problems)
        )


def _yaml_docs(content: str) -> list[dict[str, Any]]:
    try:
        return [d for d in yaml.safe_load_all(content) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


def _caddyfiles(tree: FileTree) -> list[tuple[str, str]]:
    """(where, text) for every Caddyfile in the tree: a file named Caddyfile,
    or a k8s ConfigMap's `Caddyfile` key."""
    found: list[tuple[str, str]] = []
    for path, entry in sorted(tree.files.items()):
        if path.name == "Caddyfile":
            found.append((str(path), entry.content))
        elif path.suffix in (".yaml", ".yml"):
            for doc in _yaml_docs(entry.content):
                data = doc.get("data")
                if doc.get("kind") == "ConfigMap" and isinstance(data, dict) and "Caddyfile" in data:
                    name = (doc.get("metadata") or {}).get("name", "?")
                    found.append((f"{path} (ConfigMap {name})", str(data["Caddyfile"])))
    return found


def _rendered_hosts(tree: FileTree) -> set[str]:
    """Every name a rendered service answers to inside the install."""
    hosts: set[str] = set()
    compose = tree.files.get(Path("docker-compose.yaml"))
    if compose is not None:
        for doc in _yaml_docs(compose.content):
            for name, block in (doc.get("services") or {}).items():
                hosts.add(str(name))
                block = block or {}
                if block.get("container_name"):
                    hosts.add(str(block["container_name"]))
                networks = block.get("networks")
                if isinstance(networks, dict):
                    for net in networks.values():
                        hosts.update(str(a) for a in ((net or {}).get("aliases") or []))
    for path, entry in tree.files.items():
        if path.suffix not in (".yaml", ".yml") or path == Path("docker-compose.yaml"):
            continue
        for doc in _yaml_docs(entry.content):
            if doc.get("kind") != "Service":
                continue
            meta = doc.get("metadata") or {}
            name, ns = meta.get("name"), meta.get("namespace")
            if not name:
                continue
            hosts.add(name)
            if ns:
                hosts.update({f"{name}.{ns}", f"{name}.{ns}.svc", f"{name}.{ns}.svc.cluster.local"})
    return hosts


def _upstreams(text: str) -> list[tuple[str, str, str]]:
    """(enclosing site block, directive, upstream) for each upstream-naming
    directive. The site block is the last top-level line opening a block."""
    found: list[tuple[str, str, str]] = []
    site = "?"
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not raw[:1].isspace() and line.endswith("{"):
            site = line[:-1].strip() or "?"
        tokens = line.split()
        if tokens[0] not in _UPSTREAM_DIRECTIVES:
            continue
        for token in tokens[1:]:
            if token == "{" or token.startswith("{"):
                break
            found.append((site, tokens[0], token))
    return found


def _host_of(upstream: str) -> str:
    """`http://wip-x:80/edge/ask` or `wip-x:80` → `wip-x`."""
    rest = re.sub(r"^[a-z][a-z0-9+.-]*://", "", upstream)
    rest = rest.split("/", 1)[0]
    return rest.rsplit(":", 1)[0] if ":" in rest else rest
