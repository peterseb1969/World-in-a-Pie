"""Stream an archive's documents one version chain at a time.

A merge plans documents at the logical level: a document's version rows
clash, as one chain, against the target's chain. Holding every chain in
memory to do that made a merge's peak memory the size of the archive's
documents. The backup writes documents sorted by (document_id, version), so a
chain's rows are contiguous and can be streamed: planning needs only a compact
head per document, and the rows are read again, chain by chain, where they
are checked and written.

Archives written before the export was sorted may interleave chains. Those
cannot be streamed this way; `iter_chains` raises `UnsortedArchive` on the
first out-of-order row so the caller can fall back to the in-memory path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from wip_archive.archive import ArchiveReader

# The fields merge planning reads from an archived document: the planner's id
# and logical keys plus its target projection, the namespace a redirected
# merge rewrites, and `updated_at` for the `newer` clash policy.
COMPACT_HEAD_FIELDS = (
    "document_id",
    "version",
    "namespace",
    "template_id",
    "template_version",
    "identity_hash",
    "status",
    "updated_at",
)


class UnsortedArchive(Exception):
    """The archive's documents are not grouped by document_id."""


def iter_chains(reader: ArchiveReader, namespace: str) -> Iterator[list[dict[str, Any]]]:
    """Yield each document's version rows, one chain at a time.

    Relies on the export order (document_id ascending), checked as it goes:
    a document_id smaller than its predecessor means the chains interleave.
    """
    previous: str | None = None
    chain: list[dict[str, Any]] = []
    for row in reader.read_entities("documents", namespace=namespace):
        row.pop("_id", None)
        doc_id = str(row.get("document_id", ""))
        if doc_id != previous:
            if previous is not None and doc_id < previous:
                raise UnsortedArchive(
                    f"document {doc_id!r} follows {previous!r}: the archive's "
                    "documents are not sorted by document_id"
                )
            if chain:
                yield chain
            chain = []
            previous = doc_id
        chain.append(row)
    if chain:
        yield chain


def head_of(chain: list[dict[str, Any]]) -> dict[str, Any]:
    """The chain's latest version row."""
    return max(chain, key=lambda r: r.get("version", 1))


def compact_heads(reader: ArchiveReader, namespace: str) -> list[dict[str, Any]]:
    """One compact head per document — what merge planning needs, no data."""
    return [
        {k: head[k] for k in COMPACT_HEAD_FIELDS if k in head}
        for head in map(head_of, iter_chains(reader, namespace))
    ]


ChainTransform = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def transformed_chains(
    reader: ArchiveReader, namespace: str, transform: ChainTransform
) -> Iterator[list[dict[str, Any]]]:
    """`iter_chains` with each chain passed through `transform`."""
    for chain in iter_chains(reader, namespace):
        yield transform(chain)
