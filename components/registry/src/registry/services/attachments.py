"""Checks on an attachment's name and upstream.

**Names.** An attachment owns `/apps/<name>`. It may not take a name the
install's own apps route under: the deployer renders those names into the
attach policy file (WIP_ATTACH_POLICY_FILE, beside the reservations file),
and an apply that adds an app named like an existing attachment is refused on
the deployer's side.

**Upstreams.** The router's app edge proxies users' traffic to the upstream,
from inside WIP's network. It must never become a proxy into WIP itself or
into the host's own special addresses. The authoritative check is the edge's,
when it resolves the upstream it actually connects to: the Registry and the
router need not share a network (a backend on one cluster, the umbrella on
another), and DNS can change after attach. The check here is early feedback:
it refuses an upstream that resolves, from the Registry's network, to

- an address of a host the install renders (its services, datastores,
  router, gateway, Dex), as listed in the attach policy file;
- loopback, link-local (including cloud metadata at 169.254.169.254),
  unspecified or multicast addresses, and the cloud metadata addresses
  outside link-local (AWS's IPv6 `fd00:ec2::254`, Alibaba's
  `100.100.100.200`). An IPv4-mapped or NAT64 (`64:ff9b::/96`,
  `64:ff9b:1::/48`) IPv6 address is classified as the IPv4 address it
  carries.

Resolved addresses are compared, not names, so an alias or an IP literal of
a blocked host is refused too. A host that does not resolve from here is
accepted with a warning: it may well resolve where the edge runs.

On k8s a blocked name resolves to its Service's ClusterIP, so an upstream
given as a WIP pod's IP is not caught here. The dependable control there is
a NetworkPolicy on the app edge's egress.

The policy file is read on every request (it is small and attaches are
rare), so a re-rendered file applies at once. Once WIP_ATTACH_POLICY_FILE is
set, a missing, empty or unparseable file refuses every attach: the deployer
always renders it, so its absence is a broken mount, and guessing would let
an attachment proxy into the install.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("registry.attachments")

ATTACH_POLICY_FILE_ENV = "WIP_ATTACH_POLICY_FILE"


class AttachPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocked_hosts: list[str] = Field(
        default_factory=list,
        description="Hosts of the install's own containers and services",
    )
    reserved_names: list[str] = Field(
        default_factory=list,
        description="Route names of the install's own apps (/apps/<name>)",
    )


class AttachPolicyUnavailable(Exception):
    """The configured attach policy file exists but cannot be used."""


def load_policy() -> AttachPolicy:
    """The install's attach policy. Empty only when WIP_ATTACH_POLICY_FILE is
    unset (a hand-run Registry): then only the address classes are refused.
    Once it is set, a missing, empty or unparseable file raises
    AttachPolicyUnavailable."""
    path = os.getenv(ATTACH_POLICY_FILE_ENV)
    if not path:
        return AttachPolicy()
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError as e:
        raise AttachPolicyUnavailable(f"{path} does not exist (a broken mount?)") from e
    except OSError as e:
        raise AttachPolicyUnavailable(f"cannot read {path}: {e}") from e
    if not raw.strip():
        raise AttachPolicyUnavailable(f"{path} is empty")
    try:
        return AttachPolicy.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as e:
        raise AttachPolicyUnavailable(f"{path} does not parse: {e}") from e


class UpstreamInvalid(ValueError):
    """The upstream is not a usable URL."""


def parse_upstream(upstream: str) -> tuple[str, int]:
    """(host, port) of an absolute http(s) URL without credentials."""
    try:
        parts = urlsplit(upstream)
        port = parts.port
    except ValueError as e:
        raise UpstreamInvalid(f"upstream {upstream!r} is not a valid URL: {e}") from e
    if parts.scheme not in ("http", "https"):
        raise UpstreamInvalid(f"upstream {upstream!r} must be an http:// or https:// URL")
    if not parts.hostname:
        raise UpstreamInvalid(f"upstream {upstream!r} names no host")
    if parts.username is not None or parts.password is not None:
        raise UpstreamInvalid("upstream must not carry credentials")
    if parts.query or parts.fragment:
        raise UpstreamInvalid("upstream must not carry a query or fragment")
    if parts.path not in ("", "/"):
        raise UpstreamInvalid(
            f"upstream must not carry a path ({parts.path!r}): the router proxies "
            "/apps/<name>/... to the upstream's root; use strip_prefix to drop the prefix"
        )
    return parts.hostname, port or (443 if parts.scheme == "https" else 80)


# Cloud metadata services outside the link-local range.
METADATA_ADDRESSES = frozenset({
    ipaddress.ip_address("fd00:ec2::254"),  # AWS (IPv6)
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud
})


def blocked_address_class(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why this address may never be an upstream, or None. An IPv6 address
    that carries an IPv4 one (mapped, or NAT64) is judged as that IPv4
    address, whatever the Python version's own handling."""
    ip = _unmapped(ip)
    if ip in METADATA_ADDRESSES:
        return "cloud metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local (cloud metadata lives here)"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_multicast:
        return "multicast"
    return None


async def _resolve(host: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return {ipaddress.ip_address(host)}
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout=3.0
        )
    except (OSError, TimeoutError):
        return set()
    out: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for info in infos:
        try:
            out.add(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except ValueError:
            continue
    return out


@dataclass
class UpstreamCheck:
    blocked: str | None = None
    warnings: list[str] = field(default_factory=list)


InstallAddresses = dict[ipaddress.IPv4Address | ipaddress.IPv6Address, str]


async def resolve_install_addresses(policy: AttachPolicy) -> InstallAddresses:
    """Address → name of every blocked host that resolves from here. Resolved
    once per request, not once per item."""
    names = sorted({h.lower().rstrip(".") for h in policy.blocked_hosts})
    resolved = await asyncio.gather(*(_resolve(n) for n in names))
    out: InstallAddresses = {}
    for name, ips in zip(names, resolved, strict=True):
        for ip in ips:
            out.setdefault(_unmapped(ip), name)
    return out


# NAT64 prefixes: the well-known one and the local-use one (RFC 6052, 8215).
# An address in them reaches the IPv4 address in its last 32 bits.
NAT64_PREFIXES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


def _unmapped(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """The IPv4 address an IPv6 address carries (mapped or NAT64), else ip."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if any(ip in net for net in NAT64_PREFIXES):
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


async def check_upstream(
    upstream: str, policy: AttachPolicy, install_addresses: InstallAddresses
) -> UpstreamCheck:
    """The attach-time blocklist check (advisory; see the module docstring).
    Raises UpstreamInvalid for a malformed URL."""
    host, _port = parse_upstream(upstream)
    lowered = host.lower().rstrip(".")
    blocked_names = {h.lower().rstrip(".") for h in policy.blocked_hosts}
    if lowered in blocked_names:
        return UpstreamCheck(blocked=f"{host!r} is one of the install's own hosts")

    addresses = await _resolve(host)
    if not addresses:
        return UpstreamCheck(
            warnings=[
                f"{host!r} does not resolve from the Registry's network; it is checked "
                "again where the router's app edge resolves it"
            ]
        )
    for ip in sorted(addresses, key=str):
        why = blocked_address_class(ip)
        if why:
            return UpstreamCheck(blocked=f"{host!r} resolves to {ip}, a {why} address")

    for ip in sorted(addresses, key=str):
        if _unmapped(ip) in install_addresses:
            return UpstreamCheck(
                blocked=f"{host!r} resolves to {ip}, the address of the install's own "
                f"host {install_addresses[_unmapped(ip)]!r}"
            )
    return UpstreamCheck()
