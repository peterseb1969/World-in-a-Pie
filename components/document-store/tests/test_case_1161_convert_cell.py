"""Cell conversion for the field types whose branches the suite did not reach.

Provenance: CASE-1161. Pinned before convert_cell's refactor: a date value
(not a datetime) in a date field reads as ISO, and a text cell in a datetime
field is passed on stripped, for validation to judge.
"""

from datetime import date, datetime

from document_store.services.import_service import convert_cell


def test_a_date_value_in_a_date_field_reads_as_iso():
    assert convert_cell(date(2026, 11, 8), "date") == "2026-11-08"


def test_a_text_cell_in_a_datetime_field_is_passed_on_stripped():
    assert convert_cell("  2026-11-08T10:00:00Z ", "datetime") == "2026-11-08T10:00:00Z"
    assert convert_cell(" soon ", "datetime") == "soon"
    assert convert_cell(datetime(2026, 11, 8, 10, 30), "datetime") == "2026-11-08T10:30:00"
