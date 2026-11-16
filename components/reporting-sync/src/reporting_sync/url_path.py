"""Identifiers in the URLs reporting-sync sends to other services."""

from urllib.parse import quote


def seg(identifier: str) -> str:
    """An identifier as one URL path segment.

    A term value has no character rule, and the platform addresses entities
    by value as well as by id, so an identifier may hold '/', '?', '#' or
    '%'. Interpolated raw, it is cut at '#' or '?' or read across segments
    at '/', and the target service is asked about another identifier. Every
    reserved character is encoded; ':' stays readable, legal in a segment
    and the separator of the qualified 'ns:terminology:value' form.
    """
    return quote(str(identifier), safe=":")
