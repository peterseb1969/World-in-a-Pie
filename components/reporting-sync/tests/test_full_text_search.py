"""Tests for Phase 2 of the full-text-search feature.

Covers two layers:
1. SchemaManager DDL — adds <field>_search TEXT + <field>_tsv tsvector
   GENERATED ALWAYS AS (...) STORED + GIN index for each full_text_indexed
   string field. Both on CREATE TABLE and on ALTER TABLE catch-up.
2. DocumentTransformer — populates <field>_search by running markdown
   stripping (_strip_md) over the raw value. The tsvector column is
   GENERATED, so the transformer never writes it directly.
3. _strip_md preprocessing — markdown syntax stripped, code-block
   contents preserved.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting_sync.models import (
    FieldType,
    ReportingConfig,
    SyncStrategy,
    TemplateField,
)
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.transformer import DocumentTransformer, _strip_md


# =========================================================================
# DDL: CREATE TABLE — tsvector + GIN index
# =========================================================================


def _sm() -> SchemaManager:
    return SchemaManager(MagicMock())


def test_indexed_string_field_emits_search_and_tsv_columns():
    fields = [
        TemplateField(name="title", type=FieldType.STRING, full_text_indexed=True),
        TemplateField(name="body", type=FieldType.STRING, full_text_indexed=True),
    ]
    ddl = _sm().generate_create_table_ddl("lesson", 1, fields)
    # Per-field _search column.
    assert '"title_search" TEXT' in ddl
    assert '"body_search" TEXT' in ddl
    # GENERATED tsvector column.
    assert '"title_tsv" tsvector GENERATED ALWAYS AS' in ddl
    assert '"body_tsv" tsvector GENERATED ALWAYS AS' in ddl
    # setweight wraps the tsvector — uniform 'B' weight (v1 default).
    assert "setweight(to_tsvector('english', coalesce(\"title_search\", '')), 'B')" in ddl
    assert "setweight(to_tsvector('english', coalesce(\"body_search\", '')), 'B')" in ddl


def test_indexed_field_emits_gin_index():
    fields = [TemplateField(name="body", type=FieldType.STRING, full_text_indexed=True)]
    ddl = _sm().generate_create_table_ddl("lesson", 1, fields)
    assert (
        'CREATE INDEX IF NOT EXISTS "doc_lesson_body_tsv_idx" '
        'ON "doc_lesson" USING GIN ("body_tsv")'
    ) in ddl


def test_non_indexed_string_field_emits_no_search_columns():
    fields = [TemplateField(name="body", type=FieldType.STRING)]
    ddl = _sm().generate_create_table_ddl("lesson", 1, fields)
    assert "_search" not in ddl
    assert "_tsv" not in ddl
    assert "GIN" not in ddl


def test_indexed_field_with_system_column_name_gets_data_prefix():
    """A field literally named 'status' would shadow the system column;
    the data_ prefix applies to FTS columns too so the GIN index name
    matches what the transformer actually writes to."""
    fields = [TemplateField(name="status", type=FieldType.STRING, full_text_indexed=True)]
    ddl = _sm().generate_create_table_ddl("note", 1, fields)
    assert '"data_status_search" TEXT' in ddl
    assert '"data_status_tsv" tsvector' in ddl
    assert '"doc_note_data_status_tsv_idx"' in ddl


def test_mixed_indexed_and_non_indexed_fields():
    fields = [
        TemplateField(name="tags", type=FieldType.STRING),  # plain string
        TemplateField(name="body", type=FieldType.STRING, full_text_indexed=True),
        TemplateField(name="score", type=FieldType.NUMBER),  # not indexable
    ]
    ddl = _sm().generate_create_table_ddl("article", 1, fields)
    # body gets the full set; tags/score do not.
    assert '"body_search" TEXT' in ddl
    assert '"body_tsv" tsvector' in ddl
    assert '"doc_article_body_tsv_idx"' in ddl
    assert '"tags" TEXT' in ddl
    assert '"tags_search"' not in ddl
    assert '"score" NUMERIC' in ddl


def test_indexed_relationship_template_still_gets_fts_columns():
    """FTS works on relationship templates too — the contracts compose."""
    fields = [
        TemplateField(name="source_ref", type=FieldType.REFERENCE),
        TemplateField(name="target_ref", type=FieldType.REFERENCE),
        TemplateField(name="notes", type=FieldType.STRING, full_text_indexed=True),
    ]
    ddl = _sm().generate_create_table_ddl(
        "rel_note", 1, fields, usage="relationship"
    )
    assert '"notes_tsv" tsvector' in ddl
    assert '"doc_rel_note_notes_tsv_idx"' in ddl
    # Phase 7 columns still present.
    assert '"source_ref_id" TEXT' in ddl
    assert '"target_ref_id" TEXT' in ddl


def test_full_text_columns_helper_produces_expected_pair():
    pair = SchemaManager._full_text_columns("body")
    assert pair[0] == ("body_search", "TEXT")
    name, ddl = pair[1]
    assert name == "body_tsv"
    assert ddl.startswith("tsvector GENERATED ALWAYS AS")
    assert "to_tsvector('english'" in ddl
    assert 'coalesce("body_search"' in ddl
    assert "STORED" in ddl


# =========================================================================
# DDL: ALTER TABLE — catch up existing tables
# =========================================================================


@pytest.fixture
def mock_pool_with_columns():
    """Mock pool that reports a configurable set of existing columns."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table_exists -> True

    state = {"columns": set(), "fetched_columns": False}

    async def fetch(*args, **_kwargs):
        # First fetch is the columns query in get_existing_columns.
        if not state["fetched_columns"]:
            state["fetched_columns"] = True
            return [{"column_name": c} for c in state["columns"]]
        return []

    conn.fetch = AsyncMock(side_effect=fetch)

    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn, state


@pytest.mark.asyncio
async def test_alter_table_adds_search_tsv_and_index_when_missing(mock_pool_with_columns):
    pool, conn, state = mock_pool_with_columns
    state["columns"] = {
        "document_id", "namespace", "template_id", "template_version",
        "version", "status", "identity_hash", "body",
    }

    sm = SchemaManager(pool)
    fields = [TemplateField(name="body", type=FieldType.STRING, full_text_indexed=True)]
    migrations = await sm.update_table_schema("lesson", 2, fields)

    executed = [call.args[0] for call in conn.execute.call_args_list]
    assert any('ADD COLUMN "body_search"' in s for s in executed)
    assert any('ADD COLUMN "body_tsv"' in s and "GENERATED ALWAYS AS" in s for s in executed)
    assert any(
        'CREATE INDEX IF NOT EXISTS "doc_lesson_body_tsv_idx"' in s and "GIN" in s
        for s in executed
    )
    # And the migration list includes them.
    assert any("body_search" in m for m in migrations)
    assert any("body_tsv" in m for m in migrations)


@pytest.mark.asyncio
async def test_alter_table_skips_existing_fts_columns(mock_pool_with_columns):
    pool, conn, state = mock_pool_with_columns
    # All FTS columns already in place — no ALTER needed for them.
    state["columns"] = {
        "document_id", "namespace", "template_id", "template_version",
        "version", "status", "identity_hash", "body", "body_search", "body_tsv",
    }

    sm = SchemaManager(pool)
    fields = [TemplateField(name="body", type=FieldType.STRING, full_text_indexed=True)]
    migrations = await sm.update_table_schema("lesson", 2, fields)

    executed = [call.args[0] for call in conn.execute.call_args_list]
    # No ADD COLUMN for the FTS pair…
    assert not any('ADD COLUMN "body_search"' in s for s in executed)
    assert not any('ADD COLUMN "body_tsv"' in s for s in executed)
    # …but the index is still ensured (idempotent).
    assert any(
        'CREATE INDEX IF NOT EXISTS "doc_lesson_body_tsv_idx"' in s
        for s in executed
    )


# =========================================================================
# Transformer — _strip_md preprocessing
# =========================================================================


def test_strip_md_returns_none_for_none():
    assert _strip_md(None) is None


def test_strip_md_passes_through_empty_string():
    assert _strip_md("") == ""


def test_strip_md_returns_input_for_non_string():
    assert _strip_md(42) == 42
    assert _strip_md(["a", "b"]) == ["a", "b"]


def test_strip_md_removes_headings():
    assert _strip_md("# Title").strip() == "Title"
    assert _strip_md("### Sub head").strip() == "Sub head"


def test_strip_md_unwraps_links_keeping_visible_text():
    assert "GitHub" in _strip_md("Visit [GitHub](https://github.com/) today.")
    assert "https://github.com" not in _strip_md("Visit [GitHub](https://github.com/)")


def test_strip_md_unwraps_images():
    out = _strip_md("![alt text](image.png) more text")
    assert "alt text" in out
    assert "image.png" not in out


def test_strip_md_strips_emphasis_markers():
    assert "important" in _strip_md("**important**")
    assert "*" not in _strip_md("**important**")
    assert "italic" in _strip_md("_italic_")
    assert "_" not in _strip_md("_italic_")


def test_strip_md_preserves_code_block_contents():
    """Code fences come off, but the code itself stays — so `tsvector`
    inside a code sample is still findable."""
    md = "Intro.\n\n```python\ndef tsvector_helper():\n    pass\n```\n\nMore."
    out = _strip_md(md)
    assert "tsvector_helper" in out
    assert "```" not in out


def test_strip_md_preserves_inline_code():
    out = _strip_md("Use the `to_tsvector` function.")
    assert "to_tsvector" in out
    assert "`" not in out


def test_strip_md_strips_bullets_and_numbers():
    md = "- one\n- two\n\n1. first\n2. second"
    out = _strip_md(md)
    assert "one" in out and "two" in out
    assert "first" in out and "second" in out
    # Bullet characters should be gone (at least at line starts).
    assert "- one" not in out
    assert "1. first" not in out


def test_strip_md_strips_blockquotes_and_horizontal_rules():
    md = "> quote line\n\n---\n\nbody"
    out = _strip_md(md)
    assert "quote line" in out
    assert "body" in out
    assert "---" not in out
    assert ">" not in out


# =========================================================================
# Transformer — populates <field>_search
# =========================================================================


def _doc(body: str, document_id: str = "DOC-1") -> dict:
    return {
        "document_id": document_id,
        "namespace": "wip",
        "template_id": "T-LESSON",
        "template_version": 1,
        "version": 1,
        "status": "active",
        "identity_hash": "h",
        "data": {"title": "Lesson Title", "body": body},
        "term_references": [],
        "file_references": [],
    }


def _template(*, indexed: bool = True) -> dict:
    return {
        "template_id": "T-LESSON",
        "value": "LESSON",
        "version": 1,
        "fields": [
            {"name": "title", "type": "string", "full_text_indexed": indexed},
            {"name": "body", "type": "string", "full_text_indexed": indexed},
        ],
    }


def test_transformer_populates_search_columns_for_indexed_fields():
    t = DocumentTransformer(ReportingConfig())
    rows = t.transform(_doc("# Heading\n\nA [link](https://x.com/) here."), _template())
    assert len(rows) == 1
    row = rows[0]
    assert "body_search" in row
    assert row["body_search"] is not None
    assert "Heading" in row["body_search"]
    assert "link" in row["body_search"]
    assert "https://x.com" not in row["body_search"]
    # title also indexed.
    assert "title_search" in row
    assert row["title_search"] == "Lesson Title"


def test_transformer_skips_search_columns_when_field_not_indexed():
    t = DocumentTransformer(ReportingConfig())
    rows = t.transform(_doc("body text"), _template(indexed=False))
    row = rows[0]
    assert "body_search" not in row
    assert "title_search" not in row


def test_transformer_writes_none_search_when_value_missing():
    t = DocumentTransformer(ReportingConfig())
    doc = _doc("body text")
    del doc["data"]["title"]  # title field absent in document data
    rows = t.transform(doc, _template())
    row = rows[0]
    assert "title_search" in row
    assert row["title_search"] is None


def test_transformer_does_not_emit_tsv_column():
    """The tsvector is GENERATED in Postgres — the transformer must not
    write it (would conflict with GENERATED ALWAYS)."""
    t = DocumentTransformer(ReportingConfig())
    rows = t.transform(_doc("body"), _template())
    row = rows[0]
    assert "body_tsv" not in row
    assert "title_tsv" not in row
