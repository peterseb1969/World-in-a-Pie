"""Verify an archive offline: is it consistent and restorable?

One code path for two callers: the `verify` verb, and `restore`, which runs
it before uploading. It loads the archive model with the document check on
(every document against its pinned template, its term references and its
identity hash) and reduces its findings to a verdict:

- `fail` — at least one error: the archive would not restore intact;
- `warn` — warnings but no errors;
- `pass` — nothing above info.

The same three words a transform records in its `derived_from` hop.
"""

from __future__ import annotations

from typing import Any

from wip_archive.archive import ArchiveReader
from wip_archive.model import SCHEMA_VERSION, ArchiveModel, Finding


def verify_archive(path: str, namespaces: list[str] | None = None) -> ArchiveModel:
    with ArchiveReader(path) as reader:
        return ArchiveModel.load(reader, namespaces=namespaces, check_documents=True)


def verdict(findings: list[Finding]) -> str:
    severities = {f.severity for f in findings}
    if "error" in severities:
        return "fail"
    if "warning" in severities:
        return "warn"
    return "pass"


def counts(findings: list[Finding]) -> dict[str, int]:
    out = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out


def json_payload(model: ArchiveModel) -> dict[str, Any]:
    """`verify --json`: the verdict, its findings and the document check.

    Versioned with the archive model's schema (major.minor; additions are
    optional fields only), like `inspect --json`.
    """
    findings = model.findings()
    return {
        "schema": SCHEMA_VERSION,
        "archive": model.summary(),
        "verdict": verdict(findings),
        "counts": counts(findings),
        "findings": [f.to_dict() for f in findings],
        "document_checks": (
            model.document_check.to_dict() if model.document_check is not None else None
        ),
    }
