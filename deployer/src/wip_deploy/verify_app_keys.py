"""Each app's own key stays inside its namespaces — `verify --security --live`.

An app runs with a key the deployer derives from its manifest (config_gen/
api_keys.py): `admin` on the namespaces it owns, `read`/`write` on those it
declares access to, `read` on `wip`. These checks send that key across each
boundary it must not cross and require a refusal, and pair every refusal with
a request the same key must get through, so a route that is missing or broken
cannot pass as "refused".

Requests that would change something if the boundary were broken carry a
recognisable value (`wip-verify-*`, `WIP_VERIFY_PROBE`, `v_wip_verify_probe`),
and a failure names what may have been written. Nothing is cleaned up
silently. Probes whose regression would damage the app's own data (deleting
or archiving its namespace) are not sent; the Registry's own tests cover
them.

The static check `check_rendered_master_key` reads the rendered install: only
an app that declares `privileged_key` may receive the install's master key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from wip_deploy.check_app import CheckResult
from wip_deploy.spec import App, Deployment

# The namespace a key outside the platform groups may never create: no
# install reserves it.
UNRESERVED_PROBE_NAMESPACE = "wip-verify-unreserved"
PROBE_TERMINOLOGY = "WIP_VERIFY_PROBE"
PROBE_VIEW = "v_wip_verify_probe"
# A reserved relation name: the view route refuses it with 400 after its
# permission check, so the request proves the key passed that check and
# writes nothing.
RESERVED_VIEW_NAME = "doc_wip_verify_probe"
# An entry id no install holds, for the "refused reads like missing" row.
ABSENT_ENTRY_ID = "0190c999-0000-7000-0000-0000000ff999"

_REFUSED = (403, 404)

# A namespace read on each data service. Every service resolves the key on
# its own, so one that refuses while another lets the same key through is a
# split boundary — as broken as one that lets it through everywhere — and is
# only seen by asking each of them.
NAMESPACE_READS = (
    ("def-store", "terminologies", "/api/def-store/terminologies"),
    ("template-store", "templates", "/api/template-store/templates"),
    ("document-store", "documents", "/api/document-store/documents"),
    ("reporting-sync", "reporting tables", "/api/reporting-sync/tables"),
)

NOT_PROBED = (
    "not probed: MCP calls and NATS consumers (no HTTP surface here), "
    "deleting or archiving its own namespace (a regression would destroy data)"
)


@dataclass(frozen=True)
class AppKey:
    """One enabled app's derived key, as the install renders it."""

    app: str
    key: str | None
    owned: list[str]
    reserved: dict[str, dict[str, Any]]
    readable: set[str] = field(default_factory=set)


def app_keys(install_dir: Path, deployment: Deployment, apps: list[App]) -> list[AppKey]:
    """The enabled apps' derived keys, with what each may reach.

    The privileged app has no derived key and is not listed: the static
    master-key check covers it.
    """
    from wip_deploy.config_gen.api_keys import app_api_key
    from wip_deploy.config_gen.namespaces import owned_namespaces
    from wip_deploy.verify_security import _read_secret

    refs = {r.name: r for r in deployment.spec.apps if r.enabled}
    out: list[AppKey] = []
    for app in sorted(apps, key=lambda a: a.metadata.name):
        ref = refs.get(app.metadata.name)
        if ref is None:
            continue
        spec_key = app_api_key(app, ref)
        if spec_key is None:
            continue
        owned = owned_namespaces(app, ref)
        out.append(AppKey(
            app=app.metadata.name,
            key=_read_secret(install_dir, deployment, spec_key.secret_name),
            owned=[name for name, _ in owned],
            reserved=dict(owned),
            readable=set(spec_key.namespaces),
        ))
    return out


def _is_json(response: Any) -> bool:
    return str(response.headers.get("content-type", "")).startswith("application/json")


def check_app_keys(
    base_url: str,
    keys: list[AppKey],
    admin_key: str | None,
    active: set[str],
    client: Any,
) -> list[CheckResult]:
    """One result per enabled app with a derived key."""
    if not keys:
        return []
    if not admin_key:
        return [CheckResult(
            name="app keys confined to their namespaces",
            passed=False,
            message="could not read the install's api-key to find the namespaces to probe",
            fix_hint="Pass --api-key-file with the install's admin key.",
        )]
    admin = {"X-API-Key": admin_key}
    try:
        listing = client.get(f"{base_url}/api/registry/namespaces", headers=admin)
        if listing.status_code != 200 or not _is_json(listing):
            # Not "no namespaces": the probes that need one would be skipped
            # and the check would pass having looked at nothing.
            raise RuntimeError(f"the admin key got {listing.status_code}")
        namespaces = [ns["prefix"] for ns in listing.json()]
    except Exception as e:
        return [CheckResult(
            name="app keys confined to their namespaces",
            passed=False,
            message=f"could not list the install's namespaces: {e}",
            fix_hint="Check the admin key (--api-key-file) and that the Registry is up.",
        )]
    owners = {ns: k.app for k in keys for ns in k.owned}
    return [
        _check_one(base_url, k, namespaces, owners, admin, active, client) for k in keys
    ]


def _foreign_namespace(key: AppKey, namespaces: list[str], owners: dict[str, str]) -> str | None:
    """An existing namespace the key may not read: another app's if there
    is one, else any other."""
    candidates = [ns for ns in namespaces if ns not in key.readable and ns != "wip"]
    others = [ns for ns in candidates if owners.get(ns) not in (None, key.app)]
    pick = others or candidates
    return pick[0] if pick else None


def _first_entry(base_url: str, namespace: str, admin: dict[str, str], client: Any) -> str | None:
    resp = client.get(
        f"{base_url}/api/registry/entries",
        params={"namespace": namespace, "page_size": 1},
        headers=admin,
    )
    if resp.status_code != 200 or not _is_json(resp):
        return None
    items = resp.json().get("items") or []
    return items[0].get("entry_id") if items else None


def _check_one(
    base_url: str,
    key: AppKey,
    namespaces: list[str],
    owners: dict[str, str],
    admin: dict[str, str],
    active: set[str],
    client: Any,
) -> CheckResult:
    name = f"app key '{key.app}' confined to its namespaces"
    if not key.key:
        return CheckResult(
            name=name, passed=False,
            message="the app's key is not in the install's secret backend",
            fix_hint="Re-render the install (wip-deploy redeploy) so the app's key is provisioned.",
        )
    headers = {"X-API-Key": key.key}
    problems: list[str] = []
    skipped: list[str] = []
    probed = 0
    own = next((ns for ns in key.owned if ns in namespaces), None)
    foreign = _foreign_namespace(key, namespaces, owners)

    def send(method: str, path: str, body: Any = None, auth: dict[str, str] = headers) -> Any:
        kwargs: dict[str, Any] = {"headers": auth}
        if body is not None:
            kwargs["json"] = body
        return client.request(method, f"{base_url}{path}", **kwargs)

    def refused(label: str, resp: Any, written: str | None = None) -> None:
        if resp.status_code == 401:
            problems.append(f"{label}: 401 — the service does not recognise the app's key")
        elif not (resp.status_code in _REFUSED and _is_json(resp)):
            hint = f"; it may have written {written}, remove it" if written else ""
            problems.append(f"{label}: app key got {resp.status_code} — not a refusal{hint}")

    def passes(label: str, resp: Any, expected: int = 200) -> None:
        if resp.status_code != expected:
            problems.append(
                f"{label}: got {resp.status_code}, expected {expected} — "
                "the key does not reach what it should"
            )

    try:
        # Another app's data, and an id from it.
        if foreign is None:
            skipped.append("reads across namespaces (no other namespace on this install)")
        else:
            for service, what, path in NAMESPACE_READS:
                if service in active:
                    probed += 1
                    url = f"{path}?namespace={foreign}"
                    refused(f"GET {what} in '{foreign}'", send("GET", url))
                    # The control: the admin key reads the same thing, so the
                    # route exists and the app key's refusal is a refusal.
                    passes(f"GET {what} in '{foreign}' (admin key, control)",
                           send("GET", url, auth=admin))
            if "registry" in active:
                entry = _first_entry(base_url, foreign, admin, client)
                if entry is None:
                    skipped.append(f"resolving an id from '{foreign}' (it holds no entries)")
                else:
                    probed += 1
                    hidden = send("GET", f"/api/registry/entries/{entry}")
                    absent = send("GET", f"/api/registry/entries/{ABSENT_ENTRY_ID}")
                    refused(f"GET an entry of '{foreign}'", hidden)
                    if hidden.status_code != absent.status_code:
                        problems.append(
                            f"GET an entry of '{foreign}': answered {hidden.status_code}, "
                            f"a missing entry {absent.status_code} — the status shows it exists"
                        )
                    elif _is_json(hidden) and _is_json(absent):
                        shown = json.dumps(hidden.json()).replace(entry, "<ID>")
                        missing = json.dumps(absent.json()).replace(ABSENT_ENTRY_ID, "<ID>")
                        if shown != missing:
                            problems.append(
                                f"GET an entry of '{foreign}': the refusal differs from a "
                                "missing entry's answer, so it shows the entry exists"
                            )
            if "reporting-sync" in active:
                probed += 1
                refused(
                    f"register a view in '{foreign}'",
                    send("POST", f"/api/reporting-sync/namespace/{foreign}/views",
                         {"name": PROBE_VIEW, "sql": "SELECT 1"}),
                    written=f"view '{PROBE_VIEW}' in '{foreign}'",
                )

        # What it owns: reads pass, its views pass the permission check.
        if own is None:
            skipped.append("reads of its own namespace (none exists yet: the app has not bootstrapped)")
        else:
            for service, what, path in NAMESPACE_READS:
                if service in active:
                    probed += 1
                    passes(f"GET {what} in its own '{own}'", send("GET", f"{path}?namespace={own}"))
            if "reporting-sync" in active:
                probed += 1
                passes(
                    f"view permission in its own '{own}'",
                    send("POST", f"/api/reporting-sync/namespace/{own}/views",
                         {"name": RESERVED_VIEW_NAME, "sql": "SELECT 1"}),
                    expected=400,
                )

        # The Registry's own view of the key: what /my/namespaces lists is
        # what the services that ask the Registry would allow.
        if "registry" in active:
            probed += 1
            resp = send("GET", "/api/registry/my/namespaces")
            if resp.status_code != 200 or not _is_json(resp):
                problems.append(f"GET /my/namespaces: app key got {resp.status_code}, expected 200")
            else:
                body = resp.json()
                rows = body.get("items", []) if isinstance(body, dict) else body
                listed = {n.get("prefix") for n in rows if isinstance(n, dict)}
                if foreign is not None and foreign in listed:
                    problems.append(f"GET /my/namespaces: lists '{foreign}', which the key may not read")
                if own is not None and own not in listed:
                    problems.append(f"GET /my/namespaces: does not list its own '{own}'")

        # Namespaces: none outside its reservations, and its own only as reserved.
        if "registry" in active:
            probed += 1
            resp = send("PUT", f"/api/registry/namespaces/{UNRESERVED_PROBE_NAMESPACE}", {})
            refused(
                f"create namespace '{UNRESERVED_PROBE_NAMESPACE}'", resp,
                written=f"namespace '{UNRESERVED_PROBE_NAMESPACE}'",
            )
            if key.owned:
                probed += 1
                prefix = key.owned[0]
                reserved_mode = key.reserved[prefix].get("isolation_mode", "open")
                other = "strict" if reserved_mode == "open" else "open"
                resp = send("PUT", f"/api/registry/namespaces/{prefix}", {"isolation_mode": other})
                if resp.status_code != 403:
                    hint = (
                        f"; '{prefix}' may now be isolation_mode={other} — or have been "
                        "created with it, if it did not exist yet — restore it"
                        if resp.status_code == 200 else ""
                    )
                    problems.append(
                        f"set isolation_mode={other} on its own '{prefix}' against its "
                        f"reservation ({reserved_mode}): app key got {resp.status_code}, "
                        f"expected 403{hint}"
                    )

        # The shared vocabularies: readable, never writable.
        if "def-store" in active:
            probed += 2
            passes("GET terminologies in 'wip'", send("GET", "/api/def-store/terminologies?namespace=wip"))
            refused(
                "create a terminology in 'wip'",
                send("POST", "/api/def-store/terminologies",
                     [{"namespace": "wip", "value": PROBE_TERMINOLOGY, "label": "verify probe"}]),
                written=f"terminology '{PROBE_TERMINOLOGY}' in 'wip'",
            )
    except Exception as e:  # any transport failure is a finding
        problems.append(f"request failed ({e})")

    note = "; ".join(skipped + [NOT_PROBED])
    if probed == 0 and not problems:
        problems.append("no probe ran: none of the probed services is active")
    if problems:
        return CheckResult(
            name=name, passed=False,
            message=f"{len(problems)} problem(s) across {probed} probes",
            fix_hint="\n".join(problems + [f"skipped: {s}" for s in skipped]),
        )
    return CheckResult(
        name=name, passed=True,
        message=f"{probed} probes: refused outside, passes inside ({note})",
    )


def check_rendered_master_key(
    install_dir: Path, deployment: Deployment, apps: list[App]
) -> CheckResult:
    """Only an app declaring `privileged_key` receives the master key.

    Reads what the renderer wrote, not the spec, so a renderer that
    bypasses the spec's refusal is caught here.
    """
    name = "only privileged apps hold the master key"
    if deployment.spec.modules.suppress_core:
        # Apps-only: no local Registry imports a derived key, so every app
        # runs with the install's api-key by design (config_gen/env.py).
        return CheckResult(
            name, True,
            "not applicable (apps-only install: each app runs with the install's "
            "api-key by design; the WIP it attaches to issues the keys)",
        )
    enabled = {r.name for r in deployment.spec.apps if r.enabled}
    privileged = {a.metadata.name for a in apps if a.spec.privileged_key and a.metadata.name in enabled}
    holders = _master_key_holders(install_dir, deployment, enabled)
    if holders is None:
        return CheckResult(
            name=name, passed=False,
            message="could not read the rendered install",
            fix_hint="Re-render the install (wip-deploy redeploy).",
        )
    extra = sorted(holders - privileged)
    if extra:
        return CheckResult(
            name=name, passed=False,
            message=f"{', '.join(extra)} render(s) WIP_API_KEY from the master key",
            fix_hint=(
                "Only an app declaring spec.privileged_key may hold the install's "
                "master key; re-render with the current deployer."
            ),
        )
    held = ", ".join(sorted(holders)) or "none"
    return CheckResult(name=name, passed=True, message=f"master key held by: {held}")


def _master_key_holders(
    install_dir: Path, deployment: Deployment, enabled: set[str]
) -> set[str] | None:
    """Enabled apps whose rendered WIP_API_KEY is the master key."""
    holders: set[str] = set()
    try:
        if deployment.spec.target == "k8s":
            for app in enabled:
                path = install_dir / "services" / f"{app}.yaml"
                if not path.exists():
                    continue
                for doc in yaml.safe_load_all(path.read_text()):
                    for container in _containers(doc):
                        for env in container.get("env") or []:
                            ref = ((env.get("valueFrom") or {}).get("secretKeyRef") or {})
                            if env.get("name") == "WIP_API_KEY" and ref.get("key") == "api-key":
                                holders.add(app)
            return holders
        compose = yaml.safe_load((install_dir / "docker-compose.yaml").read_text())
    except (OSError, yaml.YAMLError):
        return None
    for service, body in (compose.get("services") or {}).items():
        env = body.get("environment") or {}
        if service in enabled and isinstance(env, dict) and env.get("WIP_API_KEY") == "${API_KEY}":
            holders.add(service)
    return holders


def _containers(doc: Any) -> list[dict[str, Any]]:
    if not isinstance(doc, dict):
        return []
    template = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    return list(template.get("containers") or [])
