"""Column names cannot break out of their SQL quoting.

Template field names (and, through the transformer, document data keys)
become double-quoted column names in reporting DDL and DML. A double quote
in such a name would close the quoting and let the rest of the name be
parsed as SQL, run by the reporting writer role; a NUL truncates it. Both
are rejected at the two places column names are built. Every other
character, hyphens and spaces included, stays allowed.
"""

from unittest.mock import MagicMock

import pytest

from reporting_sync.models import FieldType, TemplateField
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.transformer import DocumentTransformer


def _sm() -> SchemaManager:
    return SchemaManager(MagicMock())


@pytest.mark.parametrize("name", ['bad"field', "bad\x00field"])
def test_table_ddl_rejects_unquotable_field_name(name):
    fields = [
        TemplateField(name="ok", type=FieldType.STRING),
        TemplateField(name=name, type=FieldType.STRING),
    ]
    with pytest.raises(ValueError, match="Unsafe SQL column name"):
        _sm().generate_create_table_ddl("testns", "probe", 1, fields)


def test_term_field_with_a_quote_is_rejected_before_its_derived_columns():
    # A term field emits <name> and <name>_term_id; both derive from the name.
    fields = [TemplateField(name='t"x', type=FieldType.TERM)]
    with pytest.raises(ValueError, match="Unsafe SQL column name"):
        _sm().generate_create_table_ddl("testns", "probe", 1, fields)


@pytest.mark.parametrize("name", ["first-name", "first name", "Ünïcode", "a'b"])
def test_table_ddl_keeps_names_quoting_can_hold(name):
    fields = [TemplateField(name=name, type=FieldType.STRING)]
    ddl = _sm().generate_create_table_ddl("testns", "probe", 1, fields)
    assert f'"{name}" TEXT' in ddl


@pytest.mark.parametrize("key", ['bad"key', "bad\x00key"])
def test_row_sql_rejects_unquotable_column(key):
    row = {"document_id": "doc-1", "version": 1, key: "value"}
    with pytest.raises(ValueError, match="Unsafe SQL column name"):
        DocumentTransformer().generate_upsert_sql('"testns"."doc_probe"', row, "latest_only")


def test_row_sql_keeps_ordinary_columns():
    row = {"document_id": "doc-1", "version": 1, "first-name": "Ada"}
    sql, values = DocumentTransformer().generate_upsert_sql(
        '"testns"."doc_probe"', row, "latest_only"
    )
    assert '"first-name"' in sql
    assert values == ["doc-1", 1, "Ada"]
