"""Tests for CASE-351 — Registry inbound side of api-key namespace forwarding.

The wip-auth side (libs/wip-auth/tests/test_case_351.py) verifies the
outbound `X-Key-Namespaces` header. These tests verify the Registry's
behaviour when that header arrives:

  - `_build_synthetic_identity` populates `raw_claims["namespaces"]`
    from the header.
  - End-to-end through `/api/registry/my/check-permission`: a scoped
    api-key gets "read" on a namespace in its list, "none" on one
    that isn't.
  - The accessible-namespaces endpoint returns only the scoped list.
  - Header-absent behaviour is unchanged from pre-fix (regression
    protection for OIDC users + privileged admin/services keys).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from registry.api.grants import _build_synthetic_identity

# ──────────────────────────────────────────────────────────────────────
# _build_synthetic_identity — pure-function unit


class TestBuildSyntheticIdentity:
    def test_header_present_populates_raw_claims(self):
        ident = _build_synthetic_identity(
            user_id="apikey:laptop-rc",
            email=None,
            groups=[],
            auth_method="api_key",
            key_namespaces_header="aa",
        )
        assert ident.raw_claims == {"namespaces": ["aa"]}

    def test_multi_namespace_csv_splits(self):
        ident = _build_synthetic_identity(
            user_id="apikey:multi",
            email=None,
            groups=[],
            auth_method="api_key",
            key_namespaces_header="aa,kb,wip",
        )
        assert ident.raw_claims == {"namespaces": ["aa", "kb", "wip"]}

    def test_header_absent_raw_claims_is_none(self):
        """No header → raw_claims=None, matching today's behaviour for
        unscoped admin/services keys and for non-api_key identities."""
        ident = _build_synthetic_identity(
            user_id="apikey:admin",
            email=None,
            groups=["wip-admins"],
            auth_method="api_key",
            key_namespaces_header=None,
        )
        assert ident.raw_claims is None

    def test_whitespace_trimmed_from_entries(self):
        ident = _build_synthetic_identity(
            user_id="apikey:t",
            email=None,
            groups=[],
            auth_method="api_key",
            key_namespaces_header="  aa , kb  ",
        )
        assert ident.raw_claims == {"namespaces": ["aa", "kb"]}

    def test_empty_fragments_dropped(self):
        """A stray comma in the header shouldn't poison the list with
        empty-string namespaces (which would fail later validation
        in confusing ways)."""
        ident = _build_synthetic_identity(
            user_id="apikey:t",
            email=None,
            groups=[],
            auth_method="api_key",
            key_namespaces_header="aa,,kb,",
        )
        assert ident.raw_claims == {"namespaces": ["aa", "kb"]}


# ──────────────────────────────────────────────────────────────────────
# End-to-end via /api/registry/my/check-permission


class TestCheckPermissionWithKeyNamespaces:
    """Exercises the bug's primary repro path: a non-privileged api-key
    with a namespace scope should get 'read' on its scoped namespaces
    (via the fallback at grants.py:121-124) and 'none' on others.

    Pre-fix: the synthetic identity had no raw_claims['namespaces'], so
    the api_key branch returned 'none' early (grants.py:85-88) and the
    fallback was unreachable.
    """

    @pytest.mark.asyncio
    async def test_scoped_key_gets_read_on_in_scope_namespace(
        self, client: AsyncClient, auth_headers: dict
    ):
        """A key scoped to 'aa' should resolve 'read' on 'aa'."""
        # Provision the namespace.
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "case351-a"},
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "case351-a",
                "user_id": "apikey:laptop-rc",
                "auth_method": "api_key",
            },
            headers={**auth_headers, "X-Key-Namespaces": "case351-a"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["permission"] == "read"
        assert body["namespace"] == "case351-a"

    @pytest.mark.asyncio
    async def test_scoped_key_gets_none_on_out_of_scope_namespace(
        self, client: AsyncClient, auth_headers: dict
    ):
        """A key scoped to 'a' must NOT have permission on 'b'.
        Least-privilege isolation — this is the principal security
        property the bundle's `permissions: read` is built on."""
        for prefix in ("case351-a2", "case351-b2"):
            await client.post(
                "/api/registry/namespaces",
                json={"prefix": prefix},
                headers=auth_headers,
            )

        resp = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "case351-b2",
                "user_id": "apikey:laptop-rc",
                "auth_method": "api_key",
            },
            headers={**auth_headers, "X-Key-Namespaces": "case351-a2"},
        )
        assert resp.status_code == 200
        assert resp.json()["permission"] == "none"

    @pytest.mark.asyncio
    async def test_multi_namespace_key_gets_read_on_each(
        self, client: AsyncClient, auth_headers: dict
    ):
        for prefix in ("case351-m1", "case351-m2"):
            await client.post(
                "/api/registry/namespaces",
                json={"prefix": prefix},
                headers=auth_headers,
            )

        for prefix in ("case351-m1", "case351-m2"):
            resp = await client.get(
                "/api/registry/my/check-permission",
                params={
                    "namespace": prefix,
                    "user_id": "apikey:multi",
                    "auth_method": "api_key",
                },
                headers={
                    **auth_headers,
                    "X-Key-Namespaces": "case351-m1,case351-m2",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["permission"] == "read", f"failed for {prefix}"

    @pytest.mark.asyncio
    async def test_no_header_non_privileged_api_key_still_gets_none(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Regression protection: a non-privileged api_key call WITHOUT
        the X-Key-Namespaces header should behave exactly like pre-fix —
        return 'none'. This is the "legacy callers" path (e.g., a future
        service that forgets to update wip-auth)."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "case351-legacy"},
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "case351-legacy",
                "user_id": "apikey:something",
                "auth_method": "api_key",
            },
            headers=auth_headers,  # no X-Key-Namespaces
        )
        assert resp.status_code == 200
        assert resp.json()["permission"] == "none"

    @pytest.mark.asyncio
    async def test_explicit_grant_still_wins_over_scope_fallback(
        self, client: AsyncClient, auth_headers: dict
    ):
        """If an admin has explicitly granted write on the namespace to
        an api-key subject, the explicit grant should still take effect
        — the namespace-list fallback only fires when no grant exists.
        This protects future grant-management UX from the fix changing
        precedence."""
        await client.post(
            "/api/registry/namespaces",
            json={"prefix": "case351-grant"},
            headers=auth_headers,
        )
        # Admin grants 'write' to this specific api-key
        await client.post(
            "/api/registry/namespaces/case351-grant/grants",
            json=[{
                "subject": "apikey:writer",
                "subject_type": "api_key",
                "permission": "write",
            }],
            headers=auth_headers,
        )

        resp = await client.get(
            "/api/registry/my/check-permission",
            params={
                "namespace": "case351-grant",
                "user_id": "apikey:writer",
                "auth_method": "api_key",
            },
            headers={**auth_headers, "X-Key-Namespaces": "case351-grant"},
        )
        assert resp.status_code == 200
        # Grant says write — the fallback would have said read.
        assert resp.json()["permission"] == "write"


# ──────────────────────────────────────────────────────────────────────
# End-to-end via /api/registry/my/accessible-namespaces


class TestAccessibleNamespacesWithKeyNamespaces:
    @pytest.mark.asyncio
    async def test_scoped_key_lists_only_in_scope_namespaces(
        self, client: AsyncClient, auth_headers: dict
    ):
        """A key scoped to ['x'] should see only 'x' in accessible-
        namespaces, even if other namespaces exist."""
        for prefix in ("case351-acc-x", "case351-acc-y", "case351-acc-z"):
            await client.post(
                "/api/registry/namespaces",
                json={"prefix": prefix},
                headers=auth_headers,
            )

        resp = await client.get(
            "/api/registry/my/accessible-namespaces",
            params={
                "user_id": "apikey:laptop",
                "auth_method": "api_key",
            },
            headers={**auth_headers, "X-Key-Namespaces": "case351-acc-x"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_superadmin"] is False
        assert set(body["namespaces"]) == {"case351-acc-x"}
        # Other namespaces exist but the key has no access — they must
        # not leak through.
        assert "case351-acc-y" not in body["namespaces"]
        assert "case351-acc-z" not in body["namespaces"]
