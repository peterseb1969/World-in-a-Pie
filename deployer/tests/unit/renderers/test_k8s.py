"""Tests for render_k8s — the Kubernetes renderer.

Same pattern as test_compose: real manifests + synthetic Deployment,
verify the shape of the rendered output via yaml.safe_load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_k8s
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    K8sPlatform,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.spec.component import Component

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.resolve()


@pytest.fixture(scope="session")
def real_discovery() -> Discovery:
    return discover(REPO_ROOT)


def _k8s_deployment(
    *,
    namespace: str = "wip-test",
    modules: list[str] | None = None,
    apps: list[str] | None = None,
    pod_dns_ndots: int | None = 1,
) -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="k8s-test"),
        spec=DeploymentSpec(
            target="k8s",
            modules={"optional": modules or ["mcp-server"]},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in (apps or [])],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip-kubi.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v1.1.0"),
            platform=PlatformSpec(
                k8s=K8sPlatform(namespace=namespace, pod_dns_ndots=pod_dns_ndots)
            ),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _secrets(
    tmp_path: Path, deployment: Deployment, discovery: Discovery
) -> ResolvedSecrets:
    return ensure_secrets(
        deployment,
        discovery.components,
        discovery.apps,
        FileSecretBackend(tmp_path / "secrets"),
    )


# ────────────────────────────────────────────────────────────────────
# Tree shape
# ────────────────────────────────────────────────────────────────────


class TestTreeShape:
    def test_standard_emits_expected_files(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        paths = {str(p) for p in tree.paths()}
        assert "namespace.yaml" in paths
        assert "secrets.yaml" in paths
        assert "configmaps.yaml" in paths
        assert "ingress.yaml" in paths
        assert "network-policies.yaml" in paths
        assert "services/registry.yaml" in paths

    def test_secrets_have_0600_mode(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        assert tree.files[Path("secrets.yaml")].mode == 0o600


# ────────────────────────────────────────────────────────────────────
# Namespace
# ────────────────────────────────────────────────────────────────────


class TestNamespace:
    def test_uses_spec_namespace(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment(namespace="wip-stable")
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        ns = yaml.safe_load(tree.files[Path("namespace.yaml")].content)
        assert ns["metadata"]["name"] == "wip-stable"


# ────────────────────────────────────────────────────────────────────
# Services
# ────────────────────────────────────────────────────────────────────


class TestComponents:
    def _parse(
        self, tmp_path: Path, discovery: Discovery, path: str, **overrides: object
    ) -> list[dict]:  # type: ignore[type-arg]
        d = _k8s_deployment(**overrides)  # type: ignore[arg-type]
        s = _secrets(tmp_path, d, discovery)
        tree = render_k8s(d, discovery.components, discovery.apps, s)
        content = tree.files[Path(path)].content
        return list(yaml.safe_load_all(content))

    def test_registry_is_deployment_not_statefulset(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        kinds = {d["kind"] for d in docs}
        assert "Deployment" in kinds
        assert "StatefulSet" not in kinds
        assert "Service" in kinds

    def test_mongodb_is_statefulset_with_pvc(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(
            tmp_path, real_discovery, "infrastructure/mongodb.yaml"
        )
        kinds = {d["kind"] for d in docs}
        assert "StatefulSet" in kinds
        assert "PersistentVolumeClaim" in kinds

    def test_image_ref_uses_registry_and_tag(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        # def-store has no per-component tag override, so it inherits
        # the global ImagesSpec.tag. Picking a component without a pin
        # keeps the assertion stable when others (registry, mcp-server)
        # bump their own tags for cache invalidation.
        docs = self._parse(tmp_path, real_discovery, "services/def-store.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        assert image == "ghcr.io/peterseb1969/def-store:v1.1.0"

    def test_image_pull_policy_always_for_wip_built(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-293: WIP-built components (build_context set) get
        imagePullPolicy=Always so same-tag re-pushes don't get the
        IfNotPresent cached-digest treatment."""
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        assert container.get("imagePullPolicy") == "Always"

    def test_image_pull_policy_omitted_for_external_images(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-293: external images (build_context=None — mongo, postgres,
        dex, etc.) keep the k8s default IfNotPresent. No registry hit on
        every pod start, no risk of stale digest since their tags are
        upstream version pins."""
        docs = self._parse(
            tmp_path, real_discovery, "infrastructure/mongodb.yaml"
        )
        statefulset = next(d for d in docs if d["kind"] == "StatefulSet")
        container = statefulset["spec"]["template"]["spec"]["containers"][0]
        assert "imagePullPolicy" not in container

    def test_k8s_target_does_not_bind_mount_wip_auth(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        """CASE-301 regression guard: only the dev target bind-mounts
        libs/wip-auth/src for live library editing. K8s target keeps the
        build-time pip install (production fidelity); pods don't have
        the operator's repo on their filesystem."""
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        for vm in container.get("volumeMounts", []):
            assert "wip-auth" not in vm.get("name", "")
            assert "wip-auth" not in vm.get("mountPath", "")
        # No PYTHONPATH override at the container level either.
        env = {e["name"]: e for e in container.get("env", [])}
        assert "PYTHONPATH" not in env

    def test_env_secrets_use_secretkeyref(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        api_key = next(e for e in env if e["name"] == "MASTER_API_KEY")
        assert api_key["valueFrom"]["secretKeyRef"]["name"] == "wip-secrets"
        assert api_key["valueFrom"]["secretKeyRef"]["key"] == "api-key"

    def test_healthcheck_renders_readiness_and_liveness(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        assert "readinessProbe" in container
        assert "livenessProbe" in container
        assert "startupProbe" in container
        assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
        assert container["livenessProbe"]["httpGet"]["path"] == "/livez"

    def test_explicit_command_rendered(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(tmp_path, real_discovery, "services/registry.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        cmd = deployment["spec"]["template"]["spec"]["containers"][0]["command"]
        assert cmd[0] == "uvicorn"
        assert "registry.main:app" in cmd

    def test_namespace_applied_to_all_resources(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        docs = self._parse(
            tmp_path, real_discovery, "services/registry.yaml",
            namespace="wip-stable",
        )
        for doc in docs:
            assert doc["metadata"]["namespace"] == "wip-stable"


# ────────────────────────────────────────────────────────────────────
# Pod DNS
# ────────────────────────────────────────────────────────────────────


class TestPodDNS:
    """The rendered env addresses peers by FQDN (four dots), which sits under
    kubelet's default ndots of 5 — so without an override every inter-service
    lookup walks all three search domains first, spending 8 DNS queries where 2
    would do."""

    def _workload(
        self, tmp_path: Path, discovery: Discovery, path: str, **overrides: object
    ) -> dict:  # type: ignore[type-arg]
        d = _k8s_deployment(**overrides)  # type: ignore[arg-type]
        s = _secrets(tmp_path, d, discovery)
        tree = render_k8s(d, discovery.components, discovery.apps, s)
        docs = list(yaml.safe_load_all(tree.files[Path(path)].content))
        return next(x for x in docs if x["kind"] in ("Deployment", "StatefulSet"))

    def test_ndots_defaults_to_one(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        wl = self._workload(tmp_path, real_discovery, "services/registry.yaml")
        assert wl["spec"]["template"]["spec"]["dnsConfig"] == {
            "options": [{"name": "ndots", "value": "1"}]
        }

    def test_ndots_applies_to_statefulsets_too(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        wl = self._workload(tmp_path, real_discovery, "infrastructure/mongodb.yaml")
        assert wl["spec"]["template"]["spec"]["dnsConfig"]["options"] == [
            {"name": "ndots", "value": "1"}
        ]

    def test_ndots_is_overridable(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        wl = self._workload(
            tmp_path, real_discovery, "services/registry.yaml", pod_dns_ndots=3
        )
        assert wl["spec"]["template"]["spec"]["dnsConfig"]["options"] == [
            {"name": "ndots", "value": "3"}
        ]

    def test_none_emits_no_dns_config(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        wl = self._workload(
            tmp_path, real_discovery, "services/registry.yaml", pod_dns_ndots=None
        )
        assert "dnsConfig" not in wl["spec"]["template"]["spec"]


# ────────────────────────────────────────────────────────────────────
# Ingress
# ────────────────────────────────────────────────────────────────────


def _k8s_front_door(
    tmp_path: Path, discovery: Discovery, components: list[Component] | None = None,
    **overrides: object,
) -> tuple[list[dict], list[dict], str]:  # type: ignore[type-arg]
    """(Ingress docs, wip-caddy docs, wip-caddy's Caddyfile) of a k8s render."""
    d = _k8s_deployment(**overrides)  # type: ignore[arg-type]
    s = _secrets(tmp_path, d, discovery)
    tree = render_k8s(d, components or discovery.components, discovery.apps, s)
    ingress = list(yaml.safe_load_all(tree.files[Path("ingress.yaml")].content))
    caddy = list(yaml.safe_load_all(tree.files[Path("services/caddy.yaml")].content))
    cm = next(x for x in caddy if x["kind"] == "ConfigMap")
    return ingress, caddy, cm["data"]["Caddyfile"]


def _handle_block(caddyfile: str, matcher: str) -> str:
    """The body of the `handle <matcher> {` block (one level)."""
    start = caddyfile.index(f"    handle {matcher} {{\n")
    end = caddyfile.index("\n    }\n", start)
    return caddyfile[start:end]


class TestIngress:
    """CASE-1067 option B: one catch-all Ingress hands every request to
    wip-caddy, the same public Caddy compose runs; every route lives in its
    Caddyfile, so any ingress controller works."""

    def test_one_ingress_to_wip_caddy(self, tmp_path: Path, real_discovery: Discovery) -> None:
        docs, _, _ = _k8s_front_door(tmp_path, real_discovery, apps=["react-console"])
        assert [d["metadata"]["name"] for d in docs] == ["wip-ingress"]
        paths = docs[0]["spec"]["rules"][0]["http"]["paths"]
        assert paths == [{
            "path": "/",
            "pathType": "Prefix",
            "backend": {"service": {"name": "wip-caddy", "port": {"number": 8080}}},
        }]
        annotations = docs[0]["metadata"]["annotations"]
        assert not any("auth-url" in k or "rewrite-target" in k or "app-root" in k for k in annotations)

    def test_tls_configured(self, tmp_path: Path, real_discovery: Discovery) -> None:
        docs, _, _ = _k8s_front_door(tmp_path, real_discovery)
        tls = docs[0]["spec"]["tls"]
        assert tls[0]["hosts"] == ["wip-kubi.local"]
        assert tls[0]["secretName"] == "wip-tls"

    def test_traefik_flavour(self, tmp_path: Path, real_discovery: Discovery) -> None:
        d = _k8s_deployment()
        assert d.spec.platform.k8s is not None
        d.spec.platform.k8s.ingress_controller = "traefik"
        d.spec.platform.k8s.ingress_class = "traefik"
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        ing = yaml.safe_load(tree.files[Path("ingress.yaml")].content)
        assert ing["spec"]["ingressClassName"] == "traefik"
        assert ing["metadata"]["annotations"] == {
            "traefik.ingress.kubernetes.io/router.entrypoints": "websecure",
            "traefik.ingress.kubernetes.io/router.tls": "true",
        }

    def test_app_route_goes_through_the_gateway(self, tmp_path: Path, real_discovery: Discovery) -> None:
        _, _, caddyfile = _k8s_front_door(tmp_path, real_discovery, apps=["react-console"])
        block = _handle_block(caddyfile, "/apps/rc/*")
        assert "forward_auth wip-auth-gateway.wip-test.svc.cluster.local:4180" in block
        assert "copy_headers X-WIP-User X-WIP-Groups" in block
        assert "request_header -X-WIP-Auth-Method" in block

    def test_api_routes_skip_the_gateway(self, tmp_path: Path, real_discovery: Discovery) -> None:
        _, _, caddyfile = _k8s_front_door(tmp_path, real_discovery)
        assert "forward_auth" not in _handle_block(caddyfile, "/api/registry/*")

    def test_plain_http_with_set_forwarded_headers(self, tmp_path: Path, real_discovery: Discovery) -> None:
        """TLS ends at the controller, so wip-caddy listens on plain HTTP and
        sets the public scheme and host itself, never trusting a client's."""
        _, _, caddyfile = _k8s_front_door(tmp_path, real_discovery, apps=["react-console"])
        assert "auto_https off" in caddyfile
        assert "\n:8080 {\n" in caddyfile
        assert "tls {" not in caddyfile
        block = _handle_block(caddyfile, "/apps/rc/*")
        assert block.count("header_up X-Forwarded-Proto https") == 2  # forward_auth + proxy
        assert block.count("header_up X-Forwarded-Host wip-kubi.local") == 2

    def test_root_redirect_to_the_app(self, tmp_path: Path, real_discovery: Discovery) -> None:
        _, _, caddyfile = _k8s_front_door(tmp_path, real_discovery, apps=["react-console"])
        assert "redir * /apps/rc/ permanent" in _handle_block(caddyfile, "/")

    def test_root_redirect_to_login_without_apps(self, tmp_path: Path, real_discovery: Discovery) -> None:
        _, _, caddyfile = _k8s_front_door(tmp_path, real_discovery)
        assert "redir * /auth/login permanent" in _handle_block(caddyfile, "/")

    def test_unrouted_paths_get_real_404s(self, tmp_path: Path, real_discovery: Discovery) -> None:
        """CASE-944 / CASE-513: never Caddy's empty 200."""
        _, _, caddyfile = _k8s_front_door(tmp_path, real_discovery)
        assert "@api_unrouted path /api /api/*" in caddyfile
        assert caddyfile.rstrip().endswith("}")
        assert "respond 404" in caddyfile or "} 404" in caddyfile

    def test_wip_caddy_workload(self, tmp_path: Path, real_discovery: Discovery) -> None:
        _, caddy, _ = _k8s_front_door(tmp_path, real_discovery)
        kinds = {x["kind"]: x for x in caddy}
        assert set(kinds) == {"ConfigMap", "Service", "Deployment"}
        dep = kinds["Deployment"]
        assert dep["metadata"]["name"] == "wip-caddy"
        assert dep["metadata"]["labels"]["app.kubernetes.io/part-of"] == "wip"
        container = dep["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "docker.io/library/caddy:2"
        assert container["volumeMounts"][0]["mountPath"] == "/etc/caddy/Caddyfile"
        assert kinds["Service"]["spec"]["ports"][0]["port"] == 8080

    def test_a_changed_route_rolls_wip_caddy(self, tmp_path: Path, real_discovery: Discovery) -> None:
        """subPath mounts never see a ConfigMap change; the Caddyfile hash
        on the pod template rolls the pod instead."""
        def hash_of(apps: list[str]) -> str:
            _, caddy, _ = _k8s_front_door(tmp_path, real_discovery, apps=apps)
            dep = next(x for x in caddy if x["kind"] == "Deployment")
            return dep["spec"]["template"]["metadata"]["annotations"]["wip.dev/config-hash"]
        assert hash_of([]) != hash_of(["react-console"])


# ────────────────────────────────────────────────────────────────────
# Inactive components
# ────────────────────────────────────────────────────────────────────


class TestActivation:
    def test_inactive_components_not_rendered(
        self, tmp_path: Path, real_discovery: Discovery
    ) -> None:
        d = _k8s_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        paths = {str(p) for p in tree.paths()}
        # reporting-sync and its deps (postgres, nats) not in standard
        assert not any("reporting-sync" in p for p in paths)
        assert not any("postgres" in p for p in paths)


# ────────────────────────────────────────────────────────────────────
# NetworkPolicies (CASE-238)
# ────────────────────────────────────────────────────────────────────


class TestNetworkPolicies:
    def _network_policies_doc(
        self, tmp_path: Path, real_discovery: Discovery,
    ) -> str:
        d = _k8s_deployment()
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        return tree.files[Path("network-policies.yaml")].content

    def test_emits_the_bundle_plus_the_app_edges_egress(
        self, tmp_path: Path, real_discovery: Discovery,
    ) -> None:
        import yaml
        doc = self._network_policies_doc(tmp_path, real_discovery)
        policies = list(yaml.safe_load_all(doc))
        # Five namespace-isolation policies, and the app edge's own egress
        # policy (the gateway is on, so the edge is rendered).
        assert len(policies) == 6
        assert all(p["kind"] == "NetworkPolicy" for p in policies)
        assert policies[-1]["metadata"]["name"] == "app-edge-egress"

    def test_includes_deny_all_default(
        self, tmp_path: Path, real_discovery: Discovery,
    ) -> None:
        import yaml
        policies = list(yaml.safe_load_all(
            self._network_policies_doc(tmp_path, real_discovery)
        ))
        deny = next(p for p in policies if p["metadata"]["name"] == "deny-all-default")
        assert set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}
        # Empty podSelector → applies to all pods
        assert deny["spec"]["podSelector"] == {}
        # No ingress/egress rules → fully blocking
        assert "ingress" not in deny["spec"]
        assert "egress" not in deny["spec"]

    def test_includes_allow_ingress_controller_with_correct_namespace_label(
        self, tmp_path: Path, real_discovery: Discovery,
    ) -> None:
        import yaml
        policies = list(yaml.safe_load_all(
            self._network_policies_doc(tmp_path, real_discovery)
        ))
        p = next(p for p in policies if p["metadata"]["name"] == "allow-ingress-controller")
        rules = p["spec"]["ingress"][0]["from"]
        assert rules[0]["namespaceSelector"]["matchLabels"][
            "kubernetes.io/metadata.name"
        ] == "ingress"
        # The controller reaches wip-caddy only: every route goes through it.
        assert p["spec"]["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "caddy"}}

    def test_traefik_controller_namespace_defaults_to_kube_system(
        self, tmp_path: Path, real_discovery: Discovery,
    ) -> None:
        import yaml
        d = _k8s_deployment()
        assert d.spec.platform.k8s is not None
        d.spec.platform.k8s.ingress_controller = "traefik"
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        policies = list(yaml.safe_load_all(tree.files[Path("network-policies.yaml")].content))
        p = next(p for p in policies if p["metadata"]["name"] == "allow-ingress-controller")
        assert p["spec"]["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"][
            "kubernetes.io/metadata.name"
        ] == "kube-system"

    def test_controller_namespace_override(self, tmp_path: Path, real_discovery: Discovery) -> None:
        import yaml
        d = _k8s_deployment()
        assert d.spec.platform.k8s is not None
        d.spec.platform.k8s.ingress_namespace = "ingress-nginx"
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        policies = list(yaml.safe_load_all(tree.files[Path("network-policies.yaml")].content))
        p = next(p for p in policies if p["metadata"]["name"] == "allow-ingress-controller")
        assert p["spec"]["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"][
            "kubernetes.io/metadata.name"
        ] == "ingress-nginx"

    def test_includes_dns_egress(
        self, tmp_path: Path, real_discovery: Discovery,
    ) -> None:
        import yaml
        policies = list(yaml.safe_load_all(
            self._network_policies_doc(tmp_path, real_discovery)
        ))
        p = next(p for p in policies if p["metadata"]["name"] == "allow-egress-dns")
        ports = p["spec"]["egress"][0]["ports"]
        protos = {(d["port"], d["protocol"]) for d in ports}
        assert (53, "UDP") in protos
        assert (53, "TCP") in protos

    def test_namespace_applied_to_all_policies(
        self, tmp_path: Path, real_discovery: Discovery,
    ) -> None:
        """All emitted policies should live in the deployment's namespace."""
        import yaml
        d = _k8s_deployment(namespace="wip-test")
        s = _secrets(tmp_path, d, real_discovery)
        tree = render_k8s(d, real_discovery.components, real_discovery.apps, s)
        doc = tree.files[Path("network-policies.yaml")].content
        for p in yaml.safe_load_all(doc):
            assert p["metadata"]["namespace"] == "wip-test"


# ────────────────────────────────────────────────────────────────────
# Preserve-prefix subpaths (CASE-248 — MinIO admin/health)
# ────────────────────────────────────────────────────────────────────


class TestPreservePrefixSubpaths:
    # MinIO was the primitive's original real user; it is retired, so a
    # synthetic optional component keeps the preserve_prefix_subpaths
    # rendering pinned (the primitive stays - any store or service whose
    # own endpoints live under its public strip prefix needs it).
    _BLOBSTORE = Component.model_validate({
        "api_version": "wip.dev/v1",
        "kind": "Component",
        "metadata": {
            "name": "blobstore",
            "category": "optional",
            "description": "synthetic strip-prefix store for the test",
        },
        "spec": {
            "image": {"name": "blobstore"},
            "ports": [{"name": "s3", "container_port": 3900}],
            "routes": [{
                "path": "/blobstore",
                "auth_required": False,
                "strip_prefix": True,
                "preserve_prefix_subpaths": [
                    "/blobstore/health", "/blobstore/admin",
                ],
            }],
        },
    })

    def _caddyfile(self, tmp_path: Path, real_discovery: Discovery) -> str:
        components = [*real_discovery.components, self._BLOBSTORE]
        _, _, caddyfile = _k8s_front_door(
            tmp_path, real_discovery, components,
            modules=["blobstore", "reporting-sync", "ingest-gateway"],
        )
        return caddyfile

    def test_preserved_subpaths_keep_their_prefix(
        self, tmp_path: Path, real_discovery: Discovery,
    ) -> None:
        """The preserved subpaths are `handle` blocks (prefix kept) and the
        rest of the store is `handle_path` (prefix stripped); Caddy's
        longest match picks the subpaths. Same Caddyfile as compose."""
        caddyfile = self._caddyfile(tmp_path, real_discovery)
        assert "    handle /blobstore/health/* {" in caddyfile
        assert "    handle /blobstore/admin/* {" in caddyfile
        assert "    handle_path /blobstore/* {" in caddyfile

    def test_no_nginx_regex_paths(self, tmp_path: Path, real_discovery: Discovery) -> None:
        components = [*real_discovery.components, self._BLOBSTORE]
        docs, _, _ = _k8s_front_door(
            tmp_path, real_discovery, components,
            modules=["blobstore", "reporting-sync", "ingest-gateway"],
        )
        assert len(docs) == 1
        assert all(p["pathType"] == "Prefix" for p in docs[0]["spec"]["rules"][0]["http"]["paths"])
