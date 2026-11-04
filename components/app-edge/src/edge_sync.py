"""The app edge's sync loop: the Registry's attachments, served by Caddy.

Every POLL_SECONDS the loop reads the active app attachments from the
Registry with the edge's own key (group `wip-app-edge`: read attachments,
nothing else), builds a Caddyfile from them, and loads it into the Caddy it
runs as a child process, through Caddy's admin API on localhost.

Rules the generated config keeps:

- **The checked address is the dialled address.** Each upstream host is
  resolved here, every resolved address is checked against the install's
  blocklist (its own hosts from WIP_ATTACH_POLICY_FILE, loopback,
  link-local, unspecified, multicast, the cloud metadata addresses, IPv6
  that carries one of those), and `reverse_proxy` dials exactly those
  addresses. The name is used only for TLS (`tls_server_name`) and, when
  the record asks, the Host header. A name that later re-resolves somewhere
  else never reaches a connection; the next sync re-checks.
- **Identity headers follow the deployer's one rule.** On a protected
  attachment the gateway's answer replaces or removes the headers it hands
  on, and the edge removes the other client identity headers; on an
  unprotected one it removes them all. The gateway's session cookie is
  removed after forward_auth has used it, so an app never receives its
  visitors' sessions.
- **The scheme and host are set, never trusted.** X-Forwarded-Proto and
  X-Forwarded-Host come from the install's public URL, toward the gateway
  and toward the app.
- **No last-known-good copy.** The routes stay valid for EXPIRY_POLLS polls
  after the last successful read; a failed poll never extends that. Past
  the deadline every attached app answers 503 until the Registry answers.

Stdlib only, so the image needs no packages beyond Python.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("app-edge")

POLL_SECONDS = float(os.getenv("POLL_SECONDS", "10"))
EXPIRY_POLLS = 3
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8080"))
ADMIN_ADDRESS = "localhost:2019"
SESSION_COOKIE = "wip_session"
CADDYFILE_PATH = "/tmp/edge.Caddyfile"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
GROUP_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")

Address = ipaddress.IPv4Address | ipaddress.IPv6Address

METADATA_ADDRESSES = frozenset({
    ipaddress.ip_address("fd00:ec2::254"),
    ipaddress.ip_address("100.100.100.200"),
})
NAT64_PREFIXES = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


# ── settings ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Settings:
    registry_url: str
    api_key: str
    gateway: str  # host:port of the auth gateway
    public_scheme: str
    public_host: str  # host[:port] as browsers see it
    gateway_headers: tuple[str, ...]
    client_headers: tuple[str, ...]
    policy_file: str | None

    @classmethod
    def from_env(cls) -> Settings:
        public = urllib.parse.urlsplit(os.environ["PUBLIC_BASE_URL"])
        gateway = urllib.parse.urlsplit(os.environ["GATEWAY_URL"])
        return cls(
            registry_url=os.environ["REGISTRY_URL"].rstrip("/"),
            api_key=os.environ["EDGE_API_KEY"],
            gateway=gateway.netloc,
            public_scheme=public.scheme,
            public_host=public.netloc,
            gateway_headers=_header_list(os.environ["GATEWAY_IDENTITY_HEADERS"]),
            client_headers=_header_list(os.environ["CLIENT_IDENTITY_HEADERS"]),
            policy_file=os.getenv("WIP_ATTACH_POLICY_FILE") or None,
        )


def _header_list(raw: str) -> tuple[str, ...]:
    headers = tuple(h.strip() for h in raw.split(",") if h.strip())
    for h in headers:
        if not re.match(r"^[A-Za-z0-9-]+$", h):
            raise ValueError(f"not a header name: {h!r}")
    return headers


# ── blocklist ──────────────────────────────────────────────────────────────


class PolicyUnavailable(Exception):
    pass


def load_blocked_hosts(path: str | None) -> list[str]:
    """The install's own hosts. Unset path: none (a hand-run edge). A set
    path that is missing, empty or unparseable fails closed."""
    if not path:
        return []
    try:
        with open(path) as f:
            raw = f.read()
    except OSError as e:
        raise PolicyUnavailable(f"cannot read {path}: {e}") from e
    if not raw.strip():
        raise PolicyUnavailable(f"{path} is empty")
    try:
        hosts = json.loads(raw)["blocked_hosts"]
    except (ValueError, KeyError, TypeError) as e:
        raise PolicyUnavailable(f"{path} does not parse: {e}") from e
    return [str(h).lower().rstrip(".") for h in hosts]


def unmapped(ip: Address) -> Address:
    """The IPv4 address an IPv6 address carries (mapped or NAT64), else ip."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if any(ip in net for net in NAT64_PREFIXES):
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def blocked_class(ip: Address) -> str | None:
    ip = unmapped(ip)
    if ip in METADATA_ADDRESSES:
        return "cloud metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_multicast:
        return "multicast"
    return None


def resolve(host: str) -> set[Address]:
    try:
        return {ipaddress.ip_address(host)}
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return set()
    out: set[Address] = set()
    for info in infos:
        try:
            out.add(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except ValueError:
            continue
    return out


def resolve_many(hosts: list[str]) -> dict[str, set[Address]]:
    if not hosts:
        return {}
    # Concurrent, so unresolvable names don't add their timeouts up. The
    # timeouts themselves are the system resolver's.
    with ThreadPoolExecutor(max_workers=min(16, len(hosts))) as pool:
        return dict(zip(hosts, pool.map(resolve, hosts), strict=True))


def reachable_families() -> set[int]:
    """The address families this container has a route for. A UDP connect
    sends nothing; it only asks the kernel for a route. An IPv4-only
    container has no route for IPv6 (ENETUNREACH), and dialling an IPv6
    address of a dual-stack upstream would fail."""
    out: set[int] = set()
    for family, probe in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 9))
            out.add(family)
        except OSError:
            continue
    return out


def _family(ip: Address) -> int:
    return socket.AF_INET6 if isinstance(ip, ipaddress.IPv6Address) else socket.AF_INET


# ── records ────────────────────────────────────────────────────────────────


@dataclass
class Target:
    """An attachment ready to serve: its record plus the checked addresses."""

    record: dict[str, Any]
    scheme: str
    host: str
    port: int
    addresses: list[Address]


@dataclass
class Plan:
    targets: list[Target] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def _upstream(record: dict[str, Any]) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(record["upstream"])
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("not an http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("credentials, query or fragment")
    if parts.path not in ("", "/"):
        raise ValueError("a path")
    host = parts.hostname
    if not (HOST_RE.match(host) or _is_ip(host)):
        raise ValueError("an unusable host")
    return parts.scheme, host, parts.port or (443 if parts.scheme == "https" else 80)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _record_ok(record: dict[str, Any]) -> str | None:
    """Defence in depth: the Registry validated the record; the edge refuses
    anything it would have to quote into the Caddyfile unsafely."""
    if not NAME_RE.match(str(record.get("name", ""))):
        return "invalid name"
    for g in record.get("allowed_groups") or []:
        if not GROUP_RE.match(str(g)):
            return "invalid group"
    for p in [*(record.get("preserve_prefix_subpaths") or []), *([record["health"]] if record.get("health") else [])]:
        if not PATH_RE.match(str(p)):
            return "invalid path"
    own = f"/apps/{record['name']}/"
    for sub in record.get("preserve_prefix_subpaths") or []:
        # A subpath outside the attachment's own prefix would take another
        # attachment's traffic to this upstream, under this one's auth.
        if not str(sub).startswith(own) or ".." in str(sub):
            return f"preserve_prefix_subpaths entry {sub!r} is outside {own}"
    if record.get("allowed_groups") and not record.get("auth_required"):
        return "allowed_groups without auth_required"
    return None


def plan(
    records: list[dict[str, Any]],
    blocked_hosts: list[str],
    families: set[int] | None = None,
) -> Plan:
    """Resolve and check every record's upstream; keep the servable ones.
    Every resolved address is checked; only those of a family the container
    can reach (`families`, default: probed) are dialled."""
    if families is None:
        families = reachable_families()
    out = Plan()
    parsed: list[tuple[dict[str, Any], str, str, int]] = []
    for r in sorted(records, key=lambda r: str(r.get("name"))):
        why = _record_ok(r)
        if why:
            out.skipped[str(r.get("name"))] = why
            continue
        try:
            scheme, host, port = _upstream(r)
        except (ValueError, KeyError) as e:
            out.skipped[r["name"]] = f"upstream is {e}"
            continue
        parsed.append((r, scheme, host, port))

    blocked_names = set(blocked_hosts)
    resolved = resolve_many(sorted({h for _, _, h, _ in parsed} | blocked_names))
    install: dict[Address, str] = {}
    for name in sorted(blocked_names):
        for ip in resolved.get(name, set()):
            install.setdefault(unmapped(ip), name)

    for r, scheme, host, port in parsed:
        name = r["name"]
        if host.lower().rstrip(".") in blocked_names:
            out.skipped[name] = f"{host} is one of the install's own hosts"
            continue
        addresses = sorted(resolved.get(host, set()), key=str)
        if not addresses:
            out.skipped[name] = f"{host} does not resolve"
            continue
        reason = None
        for ip in addresses:
            cls = blocked_class(ip)
            if cls:
                reason = f"{host} resolves to {ip}, a {cls} address"
                break
            if unmapped(ip) in install:
                reason = f"{host} resolves to {ip}, the install's own host {install[unmapped(ip)]}"
                break
        if reason:
            out.skipped[name] = reason
            continue
        dialable = [ip for ip in addresses if _family(ip) in families]
        if not dialable:
            out.skipped[name] = f"{host} resolves only to addresses this container has no route for"
            continue
        out.targets.append(Target(r, scheme, host, port, dialable))
    return out


# ── Caddyfile ──────────────────────────────────────────────────────────────


def _dial(ip: Address, port: int) -> str:
    return f"[{ip}]:{port}" if isinstance(ip, ipaddress.IPv6Address) else f"{ip}:{port}"


def _pem_to_der_b64(pem: str) -> list[str]:
    certs = re.findall(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", pem, re.S)
    out = []
    for body in certs:
        der = base64.b64decode("".join(body.split()), validate=True)
        out.append(base64.b64encode(der).decode())
    return out


def _route_block(t: Target, s: Settings, matcher: str, *, strip: bool) -> list[str]:
    r = t.record
    name = r["name"]
    lines = [f"\thandle {matcher} {{", "\t\troute {"]
    if r.get("auth_required"):
        groups = r.get("allowed_groups") or []
        uri = "/auth/verify" + (f"?allowed_groups={','.join(groups)}" if groups else "")
        lines += [
            f"\t\t\tforward_auth {s.gateway} {{",
            f"\t\t\t\turi {uri}",
            f"\t\t\t\theader_up X-Forwarded-Proto {s.public_scheme}",
            f"\t\t\t\theader_up X-Forwarded-Host {s.public_host}",
            f"\t\t\t\tcopy_headers {' '.join(s.gateway_headers)}",
            "\t\t\t\t@unauth status 401",
            "\t\t\t\thandle_response @unauth {",
            "\t\t\t\t\tredir * /auth/login?return_to={http.request.uri} 302",
            "\t\t\t\t}",
            "\t\t\t}",
        ]
        strip_headers = [h for h in s.client_headers if h not in s.gateway_headers]
    else:
        strip_headers = list(s.client_headers)
    # In a route block, directives run in the order written: these run after
    # forward_auth has copied the gateway's answer, so they must never name a
    # header the gateway supplies (see strip_headers above).
    lines += [f"\t\t\trequest_header -{h}" for h in strip_headers]
    lines += [
        f'\t\t\trequest_header Cookie "(^|;\\s*){SESSION_COOKIE}=[^;]*" ""',
        '\t\t\trequest_header Cookie "^;\\s*" ""',
    ]
    if strip:
        lines.append(f"\t\t\turi strip_prefix /apps/{name}")
    dials = " ".join(_dial(ip, t.port) for ip in t.addresses)
    lines.append(f"\t\t\treverse_proxy {dials} {{")
    lines += [
        f"\t\t\t\theader_up X-Forwarded-Proto {s.public_scheme}",
        f"\t\t\t\theader_up X-Forwarded-Host {s.public_host}",
        # A dial that fails moves on to the next checked address.
        "\t\t\t\tlb_try_duration 5s",
        "\t\t\t\tlb_try_interval 250ms",
    ]
    default_port = 443 if t.scheme == "https" else 80
    if r.get("upstream_host_header"):
        hostport = t.host if t.port == default_port else f"{t.host}:{t.port}"
        lines.append(f"\t\t\t\theader_up Host {hostport}")
    if r.get("streaming"):
        lines.append("\t\t\t\tflush_interval -1")
    if r.get("health"):
        lines += [f"\t\t\t\thealth_uri {r['health']}", "\t\t\t\thealth_interval 30s"]
    tls = r.get("upstream_tls") or {}
    if t.scheme == "https":
        lines += ["\t\t\t\ttransport http {", "\t\t\t\t\ttls"]
        if not _is_ip(t.host):
            lines.append(f"\t\t\t\t\ttls_server_name {t.host}")
        if tls.get("verify", True) is False:
            lines.append("\t\t\t\t\ttls_insecure_skip_verify")
        elif tls.get("ca_pem"):
            lines.append("\t\t\t\t\ttls_trust_pool inline {")
            lines += [f"\t\t\t\t\t\ttrust_der {der}" for der in _pem_to_der_b64(tls["ca_pem"])]
            lines.append("\t\t\t\t\t}")
        lines.append("\t\t\t\t}")
    lines += ["\t\t\t}", "\t\t}", "\t}", ""]
    return lines


def caddyfile(p: Plan | None, s: Settings, *, unavailable: str | None = None) -> str:
    """The edge's whole config. `unavailable` (or no plan yet) answers every
    /apps path with 503 and that reason."""
    lines = [
        "{",
        f"\tadmin {ADMIN_ADDRESS}",
        "\tauto_https off",
        "}",
        f":{LISTEN_PORT} {{",
        "\thandle /edge/health {",
        '\t\trespond "ok" 200',
        "\t}",
        "",
    ]
    if p is None or unavailable:
        reason = unavailable or "the app edge has not read the Registry yet"
        lines += [
            "\thandle /apps/* {",
            f'\t\trespond "Attached apps are unavailable: {reason}" 503',
            "\t}",
        ]
    else:
        for t in p.targets:
            r = t.record
            name = r["name"]
            base = f"/apps/{name}"
            strip = bool(r.get("strip_prefix"))
            if r.get("redirect_bare_path", True):
                lines += [f"\thandle {base} {{", f"\t\tredir * {base}/ permanent", "\t}", ""]
            if strip:
                for sub in r.get("preserve_prefix_subpaths") or []:
                    lines += _route_block(t, s, f"{sub}/*", strip=False)
            if not r.get("redirect_bare_path", True):
                lines += _route_block(t, s, base, strip=strip)
            lines += _route_block(t, s, f"{base}/*", strip=strip)
    lines += ["\thandle {", '\t\trespond "No app is attached at this path" 404', "\t}", "}", ""]
    return "\n".join(lines)


# ── the loop ───────────────────────────────────────────────────────────────


class RegistryUnavailable(Exception):
    """No answer, or one the edge cannot use. `reason` is what the 503 page
    and the log say."""

    def __init__(self, detail: str, reason: str = "the Registry is not answering") -> None:
        super().__init__(detail)
        self.reason = reason


def fetch_attachments(s: Settings, timeout: float = 5.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"status": "active", "page": page, "page_size": 1000})
        req = urllib.request.Request(
            f"{s.registry_url}/api/registry/attachments?{query}",
            headers={"X-API-Key": s.api_key, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RegistryUnavailable(
                    f"HTTP {e.code}", reason="the Registry refused the app edge's key"
                ) from e
            raise RegistryUnavailable(f"HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RegistryUnavailable(str(e)) from e
        out.extend(body.get("items", []))
        if page >= int(body.get("pages") or 0):
            return out
        page += 1


def load(config: str, timeout: float = 10.0) -> None:
    req = urllib.request.Request(
        f"http://{ADMIN_ADDRESS}/load",
        data=config.encode(),
        headers={"Content-Type": "text/caddyfile"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


@dataclass
class State:
    last_ok: float | None = None
    loaded: str | None = None
    plan: Plan | None = None
    reported: dict[str, str] = field(default_factory=dict)


def without_custom_cas(p: Plan) -> Plan:
    """The plan minus the attachments that carry their own CA: the fallback
    when Caddy refuses a config, so one certificate it cannot load does not
    take every attached app down."""
    keep = [t for t in p.targets if not (t.record.get("upstream_tls") or {}).get("ca_pem")]
    skipped = dict(p.skipped)
    for t in p.targets:
        if t not in keep:
            skipped[t.record["name"]] = "Caddy refused the config with its CA certificate"
    return Plan(targets=keep, skipped=skipped)


def tick(s: Settings, state: State, now: float) -> str:
    """One poll. Returns the config that should be loaded."""
    try:
        records = fetch_attachments(s)
        blocked = load_blocked_hosts(s.policy_file)
    except PolicyUnavailable as e:
        logger.error("Attach policy unavailable, serving no attached app: %s", e)
        return caddyfile(None, s, unavailable="the install's attach policy is unavailable")
    except RegistryUnavailable as e:
        logger.warning("Cannot read attachments (%s): %s", e.reason, e)
        if state.last_ok is None or now - state.last_ok > EXPIRY_POLLS * POLL_SECONDS:
            return caddyfile(None, s, unavailable=e.reason)
        return state.loaded or caddyfile(None, s)
    state.last_ok = now
    p = plan(records, blocked)
    state.plan = p
    # Reported when it changes, not on every poll.
    if p.skipped != state.reported:
        for name, why in sorted(p.skipped.items()):
            if state.reported.get(name) != why:
                logger.warning("Attachment %s not served: %s", name, why)
        for name in sorted(set(state.reported) - set(p.skipped)):
            logger.info("Attachment %s is served again", name)
        state.reported = dict(p.skipped)
    return caddyfile(p, s)


def apply(config: str, state: State, s: Settings) -> str | None:
    """Load `config`; returns what Caddy now serves. If Caddy refuses it,
    retry without the attachments carrying their own CA; if that is refused
    too, serve the unavailable config. Never keep the previous config: that
    would be a last-known-good copy, and deactivated attachments would stay
    served."""
    try:
        load(config)
        logger.info("Loaded config (%d bytes)", len(config))
        return config
    except (urllib.error.URLError, OSError) as e:
        logger.error("Caddy refused the config: %s", e)
    if state.plan is not None:
        fallback = without_custom_cas(state.plan)
        for name, why in sorted(fallback.skipped.items()):
            if why.startswith("Caddy refused"):
                logger.error("Attachment %s not served: %s", name, why)
        retry = caddyfile(fallback, s)
        try:
            load(retry)
            return retry
        except (urllib.error.URLError, OSError) as e:
            logger.error("Caddy refused the fallback config too: %s", e)
    down = caddyfile(None, s, unavailable="the app edge's config was refused")
    try:
        load(down)
        return down
    except (urllib.error.URLError, OSError) as e:
        logger.error("Caddy refused even the unavailable config: %s", e)
        return None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    state = State()
    with open(CADDYFILE_PATH, "w") as f:
        f.write(caddyfile(None, s))
    caddy = subprocess.Popen(["caddy", "run", "--config", CADDYFILE_PATH, "--adapter", "caddyfile"])

    def stop(signum: int, _frame: object) -> None:
        caddy.send_signal(signum)
        try:
            caddy.wait(timeout=10)
        finally:
            sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    time.sleep(1.0)

    while True:
        if caddy.poll() is not None:
            logger.error("Caddy exited with %s", caddy.returncode)
            return 1
        config = tick(s, state, time.monotonic())
        if config != state.loaded:
            state.loaded = apply(config, state, s)
        time.sleep(POLL_SECONDS)


# ── build-time self-test ───────────────────────────────────────────────────

# A public, self-signed certificate made for this test only; nothing trusts it.
SELF_TEST_CA = """-----BEGIN CERTIFICATE-----
MIIBXDCCAQOgAwIBAgIBATAKBggqhkjOPQQDAjA3MTUwMwYDVQQDDCxhcHAtZWRn
ZSBzZWxmLXRlc3QgQ0EgKG5vdCB0cnVzdGVkIGFueXdoZXJlKTAgFw0yNjAxMDEw
MDAwMDBaGA8yMTI1MTIwODAwMDAwMFowNzE1MDMGA1UEAwwsYXBwLWVkZ2Ugc2Vs
Zi10ZXN0IENBIChub3QgdHJ1c3RlZCBhbnl3aGVyZSkwWTATBgcqhkjOPQIBBggq
hkjOPQMBBwNCAASwc41C3KNAwQfU2g5DOp5LzP45/ADHXF/TNGPxeTlbJVUtfkdf
TthI61QNaLr1derEcwhow2rCSNXf0QdAMzkdMAoGCCqGSM49BAMCA0cAMEQCIHwY
karbw6ZtDSkDY/n42xnmA+meXxJzMdPzEupR7HHwAiArQZ2FYCskw+oxeecuAzXU
uv1qJe2gjCPT+ef3jxxb3A==
-----END CERTIFICATE-----
"""


def self_test_configs() -> dict[str, str]:
    """Generated configs exercising every option, with no DNS involved."""
    s = Settings(
        registry_url="http://wip-registry:8001",
        api_key="k",
        gateway="wip-auth-gateway:4180",
        public_scheme="https",
        public_host="wip.example:8443",
        gateway_headers=("X-WIP-User", "X-WIP-Groups"),
        client_headers=("X-WIP-User", "X-WIP-Groups", "X-WIP-Auth-Method"),
        policy_file=None,
    )
    v4, v6 = ipaddress.ip_address("203.0.113.10"), ipaddress.ip_address("2001:db8::10")

    def target(**r: Any) -> Target:
        record = {"auth_required": True, "allowed_groups": [], **r}
        scheme, host, port = _upstream(record)
        return Target(record, scheme, host, port, [v4, v6])

    full = Plan(targets=[
        target(
            name="aa", upstream="https://aa.example:9443", allowed_groups=["aa-users"],
            strip_prefix=True, preserve_prefix_subpaths=["/apps/aa/health"],
            upstream_host_header=True, streaming=True, health="/health",
            upstream_tls={"verify": True, "ca_pem": SELF_TEST_CA},
        ),
        target(name="bb", upstream="http://bb.example:3000", auth_required=False, redirect_bare_path=False),
        target(name="cc", upstream="https://cc.example", upstream_tls={"verify": False}),
    ])
    return {
        "full": caddyfile(full, s),
        "empty": caddyfile(Plan(), s),
        "unavailable": caddyfile(None, s, unavailable="the Registry is not answering"),
    }


def self_test() -> int:
    """Run `caddy validate` over every self-test config: an image whose
    generator emits a config Caddy refuses must not build."""
    import tempfile

    failed = 0
    for name, config in self_test_configs().items():
        with tempfile.NamedTemporaryFile("w", suffix=".Caddyfile", delete=False) as f:
            f.write(config)
        result = subprocess.run(
            ["caddy", "validate", "--config", f.name, "--adapter", "caddyfile"],
            capture_output=True, text=True,
        )
        os.unlink(f.name)
        if result.returncode != 0:
            failed += 1
            print(f"self-test {name}: Caddy refused the config:\n{result.stderr[-2000:]}", file=sys.stderr)
        else:
            print(f"self-test {name}: valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv[1:] else main())
