"""App attachments: an app put under the WIP umbrella through the API.

An attachment says that `/apps/<name>` on the umbrella's host is served by
`upstream`, behind the auth gateway like a deployer app's route. The app
itself runs wherever its own admin deployed it; WIP holds only this record,
never the app's container, image, environment or key plaintext. The app's
key, grants and reservations are ordinary records of their own.

The router's app edge serves `/apps/*` from these records. Attaching an app
trusts it with the sessions of everyone who visits it (it is served on WIP's
origin), so attaching needs platform admin.

An attachment is keyed by `name` and is never hard-deleted: it is
deactivated, and revived by writing it again.
"""

from datetime import UTC, datetime
from typing import ClassVar, Literal

from beanie import Document
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import IndexModel

from wip_auth.bulk_models import BulkResponseBase, BulkResultItemBase

# The group of the router's app edge key. It may read attachments and nothing
# else: no namespace scope, and not a group that may assert identities.
EDGE_READER_GROUP = "wip-app-edge"

# Lowercase letters, digits and hyphens, starting with a letter: safe as a
# URL path segment, a Caddyfile token and a DNS label.
NAME_PATTERN = r"^[a-z][a-z0-9-]{1,40}$"
GROUP_PATTERN = r"^[A-Za-z0-9._-]+$"
SUBPATH_PATTERN = r"^/[A-Za-z0-9._~/-]*$"


class UpstreamTLS(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verify: bool = Field(
        default=True, description="Verify the upstream's certificate (https upstreams)"
    )
    ca_pem: str | None = Field(
        default=None,
        description="A CA certificate (PEM) the upstream's certificate chains to, held in "
        "the record itself. Never a file reference.",
    )

    @field_validator("ca_pem")
    @classmethod
    def _is_a_certificate(cls, v: str | None) -> str | None:
        """Parsed, not just pattern-matched: the app edge loads every
        attachment into one config, and a certificate Caddy cannot parse
        would make it refuse the whole config."""
        if v is None:
            return v
        from cryptography import x509

        try:
            certs = x509.load_pem_x509_certificates(v.encode())
        except ValueError as e:
            raise ValueError(f"ca_pem is not a PEM X.509 certificate: {e}") from e
        if not certs:
            raise ValueError("ca_pem holds no certificate")
        return v


class AttachmentFields(BaseModel):
    """What an attachment declares. The route's fields mirror a deployer
    app's `Route`, so an attached app is served exactly like one. Requests
    forbid unknown fields (AttachmentRequest); the stored document keeps
    Beanie's own model config.

    A write replaces the whole record. The fields that decide who may reach
    the app (`auth_required`, `allowed_groups`) are therefore required on
    every write: a client that sends a partial record must fail, never
    silently open the app to more users by falling back to a default."""

    upstream: str = Field(
        ...,
        description="Absolute http(s) URL the router proxies to: scheme, host and "
        "optional port only, no path, credentials, query or fragment",
    )
    auth_required: bool = Field(
        ..., description="Behind the auth gateway. Required on every write."
    )
    allowed_groups: list[str] = Field(
        ...,
        description="Groups the gateway lets through ([]: any logged-in user). "
        "Requires auth_required. Required on every write.",
    )
    strip_prefix: bool = Field(
        default=False, description="Strip /apps/<name> before proxying"
    )
    preserve_prefix_subpaths: list[str] = Field(
        default_factory=list,
        description="Subpaths under the prefix that keep it when strip_prefix is set",
    )
    redirect_bare_path: bool = Field(
        default=True, description="Redirect /apps/<name> to /apps/<name>/"
    )
    upstream_host_header: bool = Field(
        default=False, description="Send the upstream's host:port as Host"
    )
    streaming: bool = Field(default=False, description="Flush responses as they arrive")
    upstream_tls: UpstreamTLS = Field(default_factory=UpstreamTLS)
    display_name: str = Field(default="", max_length=120)
    ui_only: bool = Field(default=True, description="As a deployer app's app_metadata.ui_only")
    health: str | None = Field(
        default=None,
        description="Path under the upstream that the router's edge may probe. "
        "Neither the Registry nor RC ever calls an upstream.",
    )

    @field_validator("allowed_groups")
    @classmethod
    def _groups(cls, v: list[str]) -> list[str]:
        import re

        for g in v:
            if not re.match(GROUP_PATTERN, g):
                raise ValueError(f"allowed_groups entry {g!r}: letters, digits, '.', '_', '-'")
        return v

    @field_validator("preserve_prefix_subpaths")
    @classmethod
    def _subpaths(cls, v: list[str]) -> list[str]:
        import re

        for s in v:
            if not re.match(SUBPATH_PATTERN, s):
                raise ValueError(f"preserve_prefix_subpaths entry {s!r} must be a plain path")
        return v

    @field_validator("health")
    @classmethod
    def _health(cls, v: str | None) -> str | None:
        import re

        if v is not None and not re.match(SUBPATH_PATTERN, v):
            raise ValueError("health must be a plain path starting with '/'")
        return v

    @model_validator(mode="after")
    def _groups_need_auth(self) -> "AttachmentFields":
        if self.allowed_groups and not self.auth_required:
            raise ValueError("allowed_groups requires auth_required: true")
        return self


class AppAttachment(Document, AttachmentFields):
    name: str = Field(..., pattern=NAME_PATTERN)
    status: Literal["active", "inactive"] = "active"
    managed_by: Literal["wip-deploy"] | None = Field(
        default=None,
        description="Reserved for records a deployer writes; attachments made through "
        "the API carry null",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = Field(default="system")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: str = Field(default="system")

    @property
    def path(self) -> str:
        return f"/apps/{self.name}"

    class Settings:
        name = "app_attachments"
        indexes: ClassVar[list] = [
            IndexModel([("name", 1)], unique=True, name="attachment_name_unique"),
        ]


class AttachmentRequest(AttachmentFields):
    """One item of POST /attachments: an upsert by `name`. Writing an
    inactive attachment again revives it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., pattern=NAME_PATTERN)

    @model_validator(mode="after")
    def _subpaths_are_its_own(self) -> "AttachmentRequest":
        """A preserved subpath outside /apps/<name>/ would take another
        attachment's traffic to this upstream, under this one's auth."""
        own = f"/apps/{self.name}/"
        for sub in self.preserve_prefix_subpaths:
            if not sub.startswith(own) or ".." in sub:
                raise ValueError(f"preserve_prefix_subpaths entry {sub!r} must be under {own}")
        return self


# Whether this install serves attachments at all, from the attach policy:
# "serving" (an app edge runs), "no_app_edge" (the policy says none runs), or
# "policy_unavailable" (the file cannot be used; the edge then serves nothing).
EdgeState = Literal["serving", "no_app_edge", "policy_unavailable"]
NotServedReason = Literal["inactive", "no_app_edge", "policy_unavailable"]


class AttachmentResponse(AttachmentFields):
    name: str
    path: str
    status: Literal["active", "inactive"]
    managed_by: Literal["wip-deploy"] | None = None
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str
    served: bool = Field(
        ...,
        description="The install is configured to serve this attachment: it is active and "
        "an app edge runs. Not a reachability check: the Registry never calls an "
        "upstream, and the edge may still refuse the record when it resolves it.",
    )
    not_served_reason: NotServedReason | None = Field(
        default=None,
        description="Why `served` is false: `inactive` (the record is deactivated), "
        "`no_app_edge` (this install runs no app edge), or `policy_unavailable` (the "
        "attach policy file cannot be used, so the edge serves nothing). Null when served.",
    )

    @classmethod
    def of(cls, doc: AppAttachment, edge: EdgeState) -> "AttachmentResponse":
        reason: NotServedReason | None
        if doc.status == "inactive":
            reason = "inactive"
        elif edge == "serving":
            reason = None
        else:
            reason = edge
        return cls(
            **doc.model_dump(include=set(AttachmentFields.model_fields)),
            name=doc.name,
            path=doc.path,
            status=doc.status,
            managed_by=doc.managed_by,
            created_at=doc.created_at,
            created_by=doc.created_by,
            updated_at=doc.updated_at,
            updated_by=doc.updated_by,
            served=reason is None,
            not_served_reason=reason,
        )


class AttachmentListResponse(BaseModel):
    items: list[AttachmentResponse]
    total: int
    page: int
    page_size: int
    pages: int = 0


class AttachmentBulkResultItem(BulkResultItemBase):
    name: str | None = None
    path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class AttachmentBulkResponse(BulkResponseBase[AttachmentBulkResultItem]):
    """Bulk envelope for attachment writes."""
