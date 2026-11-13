"""Client for communicating with the WIP Template Store service."""

import os
import time
from typing import Any, cast

import httpx

from wip_auth import TEMPLATE_STORE_KEY, service_api_key

# How long to cache "latest version" resolution (seconds), and how often a
# namespace's change-stamp is re-polled. A pinned (template_id, version) pair's
# SCHEMA is immutable, but its `status` is not (deactivate/reactivate), so the
# pinned cache is bounded by the namespace stamp rather than cached forever
# (CASE-490).
LATEST_TTL_SECONDS = 5.0


class TemplateStoreClient:
    """
    Client for the WIP Template Store service.

    Used to fetch templates for document validation. Templates include
    field definitions, validation rules, and identity field configuration.

    Caching strategy (version-aware):
    - (template_id, version) lookups: cached until the namespace stamp changes
      (the schema is immutable, its status is not)
    - "latest" lookups (version=None): cached with short TTL (5s) — covers
      bulk batches without going stale across template updates
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0
    ):
        """
        Initialize the Template Store client.

        Args:
            base_url: Template Store API base URL (default from env)
            api_key: API key for authentication (default from env)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or os.getenv(
            "TEMPLATE_STORE_URL",
            "http://localhost:8003"
        )
        self.api_key = api_key or service_api_key(*TEMPLATE_STORE_KEY)
        self.timeout = timeout

        # Pinned-version cache: keyed by "template_id:v{version}". The schema is
        # immutable, but `status` is not — staleness is bounded by the namespace
        # stamp (see _namespace_is_fresh), not cached forever (CASE-490).
        self._template_cache: dict[str, dict[str, Any] | None] = {}

        # TTL cache for "latest" resolution: keyed by template_id
        # Value: (template_data, timestamp)
        self._latest_cache: dict[str, tuple[dict[str, Any] | None, float]] = {}

        # Per-namespace change-stamp: namespace -> (stamp, checked_at_monotonic).
        # Polled at most once per LATEST_TTL_SECONDS per namespace; a changed
        # stamp evicts that namespace's entries from both caches (CASE-490).
        self._ns_stamp: dict[str, tuple[str, float]] = {}

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def get_cache_stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return {
            "template_cache_size": len(self._template_cache),
            "latest_cache_size": len(self._latest_cache),
            "template_cache_hits": getattr(self, '_cache_hits', 0),
            "template_cache_misses": getattr(self, '_cache_misses', 0),
        }

    def clear_cache(self):
        """Clear all template caches (mainly for testing)."""
        self._template_cache.clear()
        self._latest_cache.clear()
        self._ns_stamp.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    async def _fetch_namespace_stamp(self, namespace: str) -> str | None:
        """Fetch the template-store change-stamp for a namespace, or None on
        any error (caller treats None as 'unknown — keep serving cache')."""
        url = f"{self.base_url}/api/template-store/templates/stamp"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url, headers=self._get_headers(), params={"namespace": namespace}
                )
                if response.status_code != 200:
                    return None
                return cast(str | None, response.json().get("stamp"))
        except httpx.RequestError:
            return None

    def forget_namespace(self, namespace: str) -> None:
        """Drop everything cached for a namespace that was deleted."""
        self._evict_namespace(namespace)
        self._ns_stamp.pop(namespace, None)

    def _evict_namespace(self, namespace: str) -> None:
        """Drop every cached entry belonging to `namespace` from both caches."""
        self._template_cache = {
            k: v for k, v in self._template_cache.items()
            if not (v is not None and v.get("namespace") == namespace)
        }
        self._latest_cache = {
            k: v for k, v in self._latest_cache.items()
            if not (v[0] is not None and v[0].get("namespace") == namespace)
        }

    async def _namespace_is_fresh(self, namespace: str | None) -> None:
        """Bounded-staleness cache-coherence check for a namespace (CASE-490).

        At most once per LATEST_TTL_SECONDS per namespace, re-poll the template
        store's namespace stamp. If it changed since last seen, evict that
        namespace's entries from both caches — so a status flip (deactivate /
        reactivate) or any template change propagates within ~5s without a pod
        restart, while an unchanged namespace costs one tiny request per 5s
        regardless of how many templates it holds. A transient stamp-fetch
        failure keeps the cache (availability over freshness) and retries on the
        next lookup.
        """
        if not namespace:
            return
        now = time.monotonic()
        cached = self._ns_stamp.get(namespace)
        if cached is not None and (now - cached[1]) < LATEST_TTL_SECONDS:
            return  # checked recently — assume fresh
        stamp = await self._fetch_namespace_stamp(namespace)
        if stamp is None:
            return  # fetch failed: keep cache, don't bump checked_at (retry next time)
        # Evict only on a CHANGE from a known baseline — first sight just records
        # the baseline (entries were fetched fresh, so they're already current).
        if cached is not None and stamp != cached[0]:
            self._evict_namespace(namespace)
        self._ns_stamp[namespace] = (stamp, now)

    async def get_template(
        self,
        template_id: str | None = None,
        template_value: str | None = None,
        resolve_inheritance: bool = True,
        version: int | None = None
    ) -> dict[str, Any] | None:
        """
        Get a template by ID or value.

        Uses the same cache as get_template_resolved() when looking up by
        template_id with resolve_inheritance=True (the common case in
        reference validation).

        Args:
            template_id: Template ID
            template_value: Template value (e.g., 'PERSON')
            resolve_inheritance: If True, returns fully resolved template
            version: Specific version (None = latest)

        Returns:
            Template data if found, None otherwise
        """
        # Check cache for the common case: by template_id with inheritance
        use_cache = bool(template_id and resolve_inheritance)
        if use_cache:
            hit, entry = (
                await self._cached_version(cast(str, template_id), version)
                if version is not None
                else await self._cached_latest(cast(str, template_id))
            )
            if hit:
                self._cache_hits = getattr(self, '_cache_hits', 0) + 1
                return entry

        url = self._template_url(template_id, template_value, resolve_inheritance)
        if url is None:
            return None
        if use_cache:
            self._cache_misses = getattr(self, '_cache_misses', 0) + 1

        result = await self._fetch_template(url, version)
        if use_cache and result is not None:
            self._cache_template(cast(str, template_id), version, result)
        return result

    async def _cached_version(
        self, template_id: str, version: int,
    ) -> tuple[bool, dict[str, Any] | None]:
        """A pinned version from the cache, after the namespace freshness
        check (which may evict it)."""
        cache_key = f"{template_id}:v{version}"
        entry = self._template_cache.get(cache_key)
        if entry is None:
            return False, None
        await self._namespace_is_fresh(entry.get("namespace"))
        entry = self._template_cache.get(cache_key)
        return entry is not None, entry

    async def _cached_latest(self, template_id: str) -> tuple[bool, dict[str, Any] | None]:
        """The latest version from the cache while it is younger than
        LATEST_TTL_SECONDS, after the namespace freshness check (which may
        evict it)."""
        cached = self._latest_cache.get(template_id)
        if cached is None or (time.monotonic() - cached[1]) >= LATEST_TTL_SECONDS:
            return False, None
        if cached[0] is not None:
            await self._namespace_is_fresh(cached[0].get("namespace"))
            cached = self._latest_cache.get(template_id)
        if cached is not None and (time.monotonic() - cached[1]) < LATEST_TTL_SECONDS:
            return True, cached[0]
        return False, None

    def _template_url(
        self, template_id: str | None, template_value: str | None, resolve_inheritance: bool,
    ) -> str | None:
        """By id or by value; the /{id} endpoint always resolves inheritance,
        /{id}/raw does not. None when neither is given."""
        base = f"{self.base_url}/api/template-store/templates"
        if template_id:
            return f"{base}/{template_id}" if resolve_inheritance else f"{base}/{template_id}/raw"
        if template_value:
            return f"{base}/by-value/{template_value}"
        return None

    async def _fetch_template(self, url: str, version: int | None) -> dict[str, Any] | None:
        """GET one template: None for a 404, TemplateStoreError for anything
        else that is not a 200 (a 409 with the store's own reason)."""
        params = {"version": version} if version is not None else None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self._get_headers(), params=params)
        except httpx.RequestError as e:
            raise TemplateStoreError(f"Request failed: {e!s}", unavailable=True) from e
        if response.status_code == 404:
            return None
        if response.status_code == 409:
            raise _conflict_error(response)
        if response.status_code != 200:
            raise TemplateStoreError(
                f"Failed to get template: {response.status_code} - {response.text}",
                unavailable=True,
            )
        return cast(dict[str, Any] | None, response.json())

    def _cache_template(self, template_id: str, version: int | None, result: dict[str, Any]) -> None:
        """A pinned fetch fills its version's entry; a latest fetch fills the
        latest entry and the entry of the version it returned."""
        if version is not None:
            self._template_cache[f"{template_id}:v{version}"] = result
            return
        self._latest_cache[template_id] = (result, time.monotonic())
        actual_version = result.get("version")
        if actual_version is not None:
            self._template_cache[f"{template_id}:v{actual_version}"] = result

    async def get_template_resolved(
        self,
        template_id: str,
        version: int | None = None
    ) -> dict[str, Any] | None:
        """
        Get a template with inheritance fully resolved.

        Caching strategy:
        - Pinned version (version != None): cached until the namespace stamp
          changes (the schema is immutable, its status is not)
        - Latest (version == None): cached with short TTL (5s) — fast enough
          for bulk batches, fresh enough for template updates

        Args:
            template_id: Template ID
            version: Specific version (None = latest)

        Returns:
            Resolved template data if found, None otherwise
        """
        now = time.monotonic()

        # --- Pinned version: namespace-stamp-bounded cache ---
        if version is not None:
            cache_key = f"{template_id}:v{version}"
            entry = self._template_cache.get(cache_key)
            if entry is not None:
                # Bound `status` staleness: a changed namespace stamp evicts
                # this entry, forcing a re-fetch (CASE-490).
                await self._namespace_is_fresh(entry.get("namespace"))
                entry = self._template_cache.get(cache_key)
                if entry is not None:
                    self._cache_hits = getattr(self, '_cache_hits', 0) + 1
                    return entry
        else:
            # --- Latest: TTL cache, also namespace-stamp gated ---
            cached = self._latest_cache.get(template_id)
            if cached is not None and (now - cached[1]) < LATEST_TTL_SECONDS:
                if cached[0] is not None:
                    await self._namespace_is_fresh(cached[0].get("namespace"))
                    cached = self._latest_cache.get(template_id)
                if cached is not None and (time.monotonic() - cached[1]) < LATEST_TTL_SECONDS:
                    self._cache_hits = getattr(self, '_cache_hits', 0) + 1
                    return cached[0]

        # Fetch from service
        self._cache_misses = getattr(self, '_cache_misses', 0) + 1
        template = await self.get_template(
            template_id=template_id,
            resolve_inheritance=True,
            version=version
        )

        # Cache the result
        if version is not None:
            # Pinned version: namespace-stamp bounded (a miss is not kept:
            # the read above treats a cached None as a miss)
            self._template_cache[f"{template_id}:v{version}"] = template
        elif template is not None:
            # Latest: TTL cache. A miss is not cached, so a template created
            # a moment later is found at once, not up to the TTL later.
            self._latest_cache[template_id] = (template, now)
            # Also populate the pinned cache for the resolved version
            actual_version = template.get("version")
            if actual_version is not None:
                self._template_cache[f"{template_id}:v{actual_version}"] = template

        return template

    async def template_exists(
        self,
        template_ref: str
    ) -> bool:
        """
        Check if a template exists by ID or code.

        Args:
            template_ref: Template ID or code

        Returns:
            True if template exists and is active
        """
        # Try as ID first, then as value
        template = await self.get_template(template_id=template_ref)
        if template is None:
            template = await self.get_template(template_value=template_ref)

        if template is None:
            return False

        # Check if active
        return template.get("status") == "active"

    async def get_template_descendants(
        self,
        template_id: str
    ) -> list[dict[str, Any]]:
        """
        Get all templates that inherit from this template (directly or indirectly).

        Args:
            template_id: Template ID to get descendants for

        Returns:
            List of descendant template dicts
        """
        url = f"{self.base_url}/api/template-store/templates/{template_id}/descendants"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers()
                )

                if response.status_code == 404:
                    return []

                if response.status_code != 200:
                    raise TemplateStoreError(
                        f"Failed to get descendants: {response.status_code} - {response.text}",
                        unavailable=True,
                    )

                data = response.json()
                return cast(list[dict[str, Any]], data.get("items", []))
        except httpx.RequestError as e:
            raise TemplateStoreError(f"Request failed: {e!s}", unavailable=True) from e

    async def validate_template_references(
        self,
        template_ids: list[str]
    ) -> dict[str, bool]:
        """
        Check if multiple templates exist.

        Args:
            template_ids: List of template IDs to check

        Returns:
            Dict mapping template_id to existence status
        """
        results = {}
        for template_id in template_ids:
            results[template_id] = await self.template_exists(template_id)
        return results

    async def health_check(self) -> bool:
        """Check if the Template Store service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


class TemplateStoreError(Exception):
    """Error communicating with the Template Store service.

    ``code`` carries Template Store's own ``error_code`` when it refused for a
    reason (e.g. ``inheritance_unresolved``), so callers can tell a template
    that cannot be served from an outage. ``unavailable`` is True when the
    store could not be asked or answered with an error status: a caller that
    gates a write refuses it (DependencyUnavailableError) instead of reading
    it as "not found".
    """

    def __init__(self, message: str, code: str | None = None, unavailable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.unavailable = unavailable


# Singleton instance for convenience
_client: TemplateStoreClient | None = None


def get_template_store_client() -> TemplateStoreClient:
    """Get the singleton Template Store client instance."""
    global _client
    if _client is None:
        _client = TemplateStoreClient()
    return _client


def configure_template_store_client(
    base_url: str | None = None,
    api_key: str | None = None
) -> TemplateStoreClient:
    """Configure and return the Template Store client."""
    global _client
    _client = TemplateStoreClient(base_url=base_url, api_key=api_key)
    return _client


def _conflict_error(response: httpx.Response) -> TemplateStoreError:
    """A 409's TemplateStoreError, carrying the store's detail and
    error_code when its body is a JSON object, its text otherwise."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return TemplateStoreError(
        str(body.get("detail") or response.text),
        code=body.get("error_code"),
    )
