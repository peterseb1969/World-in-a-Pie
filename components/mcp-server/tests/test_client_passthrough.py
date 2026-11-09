"""What the MCP client sends and returns: the default namespace, identifiers
left for the service to resolve, qualifications passed through, paginated
lists, CSV bodies and per-item error details."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wip_mcp.client import BulkError, WipClient
from wip_mcp.server import _error, _truncate_csv


def _client(default_namespace: str | None = None) -> WipClient:
    client = WipClient(
        registry_url="http://reg", def_store_url="http://def",
        template_store_url="http://tpl", document_store_url="http://doc",
        reporting_sync_url="http://rep", api_key="k", verify_tls=True,
    )
    client.default_namespace = default_namespace
    return client


def _http(body=None, text: str = "") -> AsyncMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.is_success = True
    resp.json.return_value = body if body is not None else {"items": [], "results": []}
    resp.text = text
    http = AsyncMock()
    http.get.return_value = resp
    http.post.return_value = resp
    http.request.return_value = resp
    return http


# -- WIP_MCP_DEFAULT_NAMESPACE ------------------------------------------------


@pytest.mark.asyncio
async def test_an_omitted_namespace_takes_the_default_on_a_read():
    client = _client("clinic")
    http = _http()
    with patch.object(client, "_get_client", return_value=http):
        await client.list_documents()
    assert http.get.call_args.kwargs["params"]["namespace"] == "clinic"


@pytest.mark.asyncio
async def test_an_explicit_namespace_wins_over_the_default():
    client = _client("clinic")
    http = _http()
    with patch.object(client, "_get_client", return_value=http):
        await client.list_documents(namespace="other")
    assert http.get.call_args.kwargs["params"]["namespace"] == "other"


@pytest.mark.asyncio
async def test_no_default_leaves_an_optional_namespace_out():
    client = _client(None)
    http = _http()
    with patch.object(client, "_get_client", return_value=http):
        await client.list_documents()
    assert "namespace" not in http.get.call_args.kwargs["params"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.get_term_children("t"),
        lambda c: c.get_term_parents("t"),
        lambda c: c.get_term_ancestors("t"),
        lambda c: c.get_term_descendants("t"),
        lambda c: c.create_term_relations([]),
        lambda c: c.delete_term_relations([]),
        lambda c: c.activate_template("t"),
    ],
)
async def test_a_route_that_needs_a_namespace_gets_the_default_or_refuses(call):
    client = _client("clinic")
    http = _http()
    with patch.object(client, "_get_client", return_value=http):
        await call(client)
    sent = http.get.call_args or http.post.call_args or http.request.call_args
    assert sent.kwargs["params"]["namespace"] == "clinic"

    with pytest.raises(ValueError, match="namespace is required"):
        await call(_client(None))


@pytest.mark.asyncio
async def test_run_report_query_body_takes_the_default_namespace():
    client = _client("clinic")
    http = _http({"rows": []})
    with patch.object(client, "_get_client", return_value=http):
        await client.run_report_query("SELECT 1")
    assert http.post.call_args.kwargs["json"]["namespace"] == "clinic"


# -- identifiers go to the service unresolved ---------------------------------


@pytest.mark.asyncio
async def test_create_terms_sends_the_terminology_as_given_with_its_namespace():
    """The route resolves it through the Registry (synonyms, ns:VALUE);
    no local by-value lookup first."""
    client = _client(None)
    http = _http({"results": [], "total": 0})
    with patch.object(client, "_get_client", return_value=http):
        await client.create_terms("kb:CASE STATUS", [{"value": "x"}], namespace="kb")
    assert http.get.call_count == 0
    url = http.post.call_args.args[0]
    assert url == "http://def/api/def-store/terminologies/kb%3ACASE%20STATUS/terms"
    assert http.post.call_args.kwargs["params"]["namespace"] == "kb"


@pytest.mark.asyncio
async def test_lookup_by_id_forwards_namespace_and_entity_type():
    client = _client("ignored-default")
    http = _http({"results": [{"status": "found"}]})
    with patch.object(client, "_get_client", return_value=http):
        await client.lookup_by_id("PATIENT", namespace="clinic", entity_type="templates")
    assert http.post.call_args.kwargs["json"] == [
        {"entry_id": "PATIENT", "namespace": "clinic", "entity_type": "templates"},
    ]
    with patch.object(client, "_get_client", return_value=http):
        await client.lookup_by_id("019a0000-0000-7000-8000-000000000000")
    # A canonical id resolves anywhere: the default namespace is not sent.
    assert http.post.call_args.kwargs["json"] == [
        {"entry_id": "019a0000-0000-7000-8000-000000000000"},
    ]


@pytest.mark.asyncio
async def test_import_documents_csv_resolves_the_template_in_the_target_namespace(tmp_path):
    from wip_mcp import server

    f = tmp_path / "rows.csv"
    f.write_text("name\nA\n")
    mock = AsyncMock()
    mock._ns = lambda ns: ns or "clinic"
    mock.get_template_by_value.return_value = {"template_id": "T1", "fields": []}
    mock.import_documents.return_value = {"created": 1}
    with patch("wip_mcp.server.get_client", return_value=mock):
        await server.import_documents_csv(
            str(f), "PATIENT", column_mapping={"name": "name"}, namespace="clinic",
        )
    mock.get_template_by_value.assert_awaited_once_with(value="PATIENT", namespace="clinic")
    assert mock.import_documents.call_args.kwargs["template_id"] == "T1"
    assert mock.import_documents.call_args.kwargs["namespace"] == "clinic"


# -- lists say when they are partial ------------------------------------------


@pytest.mark.asyncio
async def test_list_api_keys_pages_and_keeps_the_envelope():
    envelope = {"items": [{"name": "a"}], "total": 120, "page": 2, "page_size": 50, "pages": 3}
    client = _client(None)
    http = _http(envelope)
    with patch.object(client, "_get_client", return_value=http):
        data = await client.list_api_keys(page=2)
    assert data == envelope
    assert http.get.call_args.kwargs["params"] == {"page": 2, "page_size": 50}


# -- content types ------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_terminology_csv_returns_the_text():
    client = _client(None)
    http = _http(text="value,label\r\nGB,United Kingdom\r\n")
    http.get.return_value.json.side_effect = ValueError("not JSON")
    with patch.object(client, "_get_client", return_value=http):
        data = await client.export_terminology("COUNTRY", format="csv")
    assert data == "value,label\r\nGB,United Kingdom\r\n"


# -- error details reach the agent --------------------------------------------


def test_a_single_item_error_keeps_its_details():
    client = _client(None)
    referrers = {"rule": "referenced", "referrers": [{"namespace": "a", "count": 2}]}
    with pytest.raises(BulkError) as exc:
        client._unwrap_single({"results": [{
            "status": "error", "error": "still referenced",
            "error_code": "invalid_state", "details": referrers,
        }]})
    out = _error(exc.value)
    assert out.startswith("WIP error [invalid_state]: still referenced")
    assert json.loads(out.split("details: ", 1)[1]) == referrers


# -- CSV preview is cut at records, not lines ---------------------------------


def _csv(rows: int, cell: str = "x") -> str:
    lines = ["id,note"] + [f'{i},"{cell}"' for i in range(rows)]
    return "\r\n".join(lines) + "\r\n"


def test_an_export_within_the_limit_comes_back_whole():
    """Its trailing line terminator and line breaks inside quoted cells do
    not count as rows."""
    assert _truncate_csv(_csv(100), 100) == _csv(100)
    multi = _csv(60, cell="line one\nline two")
    assert _truncate_csv(multi, 100) == multi


def test_a_longer_export_is_cut_at_a_record_and_counts_rows():
    content = _csv(101, cell="line one\nline two")
    out = _truncate_csv(content, 100)
    body, note = out.rsplit("\n... ", 1)
    assert note == "truncated: first 100 of 101 rows"
    import csv
    import io
    rows = list(csv.reader(io.StringIO(body.rstrip("\n"), newline="")))
    assert len(rows) == 101  # header + 100 complete records
    assert rows[-1] == ["99", "line one\nline two"]
